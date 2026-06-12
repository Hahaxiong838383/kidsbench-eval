"""Memobase（memodb-io/memobase）的 KidsBench 契约适配。

范式定位：**用户画像中心（profile-centric）**——LLM 异步提取「属性 + 事件」双层
结构化画像。当前 6+2 家全是「事件/事实」离散存储，没人做兴趣演化/学习风格/情绪基线
的画像沉淀。主场 = T5 长程画像一致性 + 兴趣演化类（写极多读极稀的画像沉淀）。

⚠️ 读取范式与众不同：read 不是「检索 top-k 记忆」，而是「读取当前用户画像状态」
（profile 是 LLM merge 后的派生物，不是原始 turn）。adapter 把画像条目包装成 Memory。

接入要点（全部来自 Phase 0 实测，见 docs/MEMOBASE_VERIFIED_FACTS.md）：
1. 部署：FastAPI + pg0 嵌入式 PG + redis，scripts/setup_memobase_server.sh
2. 中文原生最优（language: zh 一等公民，零 patch）
3. write=insert(ChatBlob, created_at=虚拟时间)；flush=flush(sync=True) 同步等画像就绪
4. read=context(chats=[query]) 拿 profile+event 组合包，或 profile() 全量画像
5. clear=delete_user（每题独立 user）
6. 溯源仅 date-level（画像是 LLM merge 派生，同日多 turn 不能唯一绑定）→ wrapped declared-weak
7. 幂等：blob 层非幂等但画像层 LLM merge 兜住（实测重复写画像不翻倍）；仍 sidecar 查重省成本

⚠️ 已知约束（codex 对抗审记录）：user_id→memobase uid 映射 + _seen 查重都在进程内内存。
评测协议串行单进程、用完即 clear，约束成立；server 重启后 adapter 状态丢失（旧 uid 找不到→
clear 静默成功但服务端数据残留）——评测每轮新建 adapter 不复用旧 user，不踩此坑；生产需把
user_id↔uid 映射持久化或改用确定性 external_id。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from ..contract import (
    AdapterError,
    ClearStats,
    FlushStats,
    Memory,
    MemoryAdapter,
    ReadOpts,
    ReadResult,
    Turn,
    WriteStats,
)
from ..middleware import (
    AuthError,
    NetworkError,
    RateLimitError,
    StructuredLogger,
    TimeoutError_,
    track_metrics,
    wrap_errors,
)

_ERROR_MAPPING = {
    "httpx.TimeoutException": TimeoutError_,
    "httpx.ReadTimeout": TimeoutError_,
    "httpx.ConnectError": NetworkError,
    "httpx.HTTPStatusError": NetworkError,
    "openai.RateLimitError": RateLimitError,
    "openai.AuthenticationError": AuthError,
}


class MemobaseAdapter(MemoryAdapter):
    """Translate Memobase profile/event APIs into KidsBench adapter contract."""

    name = "memobase"
    paradigm_tags = {
        "representation": "user_profile+events",
        "retrieval": "profile_state_read",
        "write_policy": "async_profile_extraction",
        "controller": "llm",
        "cognitive": ["semantic", "episodic"],
    }

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        client: Any | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        """config 键：
        base_url: memobase server（默认 http://127.0.0.1:8019）
        api_key: ACCESS_TOKEN（默认 kb-phase0-secret）
        max_token_size: profile/context 读取上限（默认 1500）
        """
        self._config = dict(config or {})
        self._logger = logger or StructuredLogger(self.name)
        self._client = client
        # user_id（评测键）→ memobase uid；每个评测 user 一个 memobase user
        self._uids: dict[str, str] = {}
        self._seen: dict[str, set[str]] = {}
        self._stats = {"total_writes": 0, "total_reads": 0, "dedup_hits": 0}

    # ------------------------------------------------------------ client

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from memobase import MemoBaseClient
            except Exception as err:  # pragma: no cover - 依赖检查
                raise AdapterError("memobase not installed; run: pip install memobase") from err
            self._client = MemoBaseClient(
                project_url=self._config.get("base_url", "http://127.0.0.1:8019"),
                api_key=self._config.get("api_key", "kb-phase0-secret"),
            )
        return self._client

    def _ensure_uid(self, user_id: str) -> str:
        if user_id in self._uids:
            return self._uids[user_id]
        client = self._ensure_client()
        uid = client.add_user({"name": user_id})
        self._uids[user_id] = uid
        return uid

    def _user(self, user_id: str) -> Any:
        return self._ensure_client().get_user(self._ensure_uid(user_id))

    @staticmethod
    def _make_chat_blob(messages: list[dict]) -> Any:
        """构造 memobase ChatBlob（测试可覆写注入 fake，避免装 memobase）。"""
        from memobase import ChatBlob

        return ChatBlob(messages=messages)

    @staticmethod
    def _ts_str(turn: Turn) -> str:
        """Turn.timestamp（unix float）→ "YYYY-MM-DD HH:MM:SS"（虚拟时钟注入位）。"""
        return datetime.fromtimestamp(turn.timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    # ------------------------------------------------------------ contract

    @track_metrics(method="write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        """insert ChatBlob，created_at=虚拟时间。画像抽取在 flush(sync) 时做。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.perf_counter()
        seen = self._seen.setdefault(user_id, set())
        if turn.turn_id in seen:
            self._stats["dedup_hits"] += 1
            return WriteStats(
                success=True, latency_ms=(time.perf_counter() - t0) * 1000,
                cost_token=0, raw={"deduplicated": True},
            )
        user = self._user(user_id)
        user.insert(self._make_chat_blob([{
            "role": turn.role or "user",
            "content": turn.text,
            "created_at": self._ts_str(turn),
        }]))
        seen.add(turn.turn_id)
        self._stats["total_writes"] += 1
        return WriteStats(
            success=True, latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,  # 抽取在 flush 时做，insert 仅入 buffer
        )

    @track_metrics(method="flush")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def flush(self, user_id: str) -> FlushStats:
        """flush(sync=True)：同步等画像抽取完成（实测 ~14s/批，含 LLM 调用）。

        注意：Memobase 的 flush 触发 LLM 画像抽取，不是「轻量索引就绪」——
        但契约要求 read 前画像必须就位，profile 抽取就在这步，故归入 flush
        （consolidate 留 no-op）。abstract flush 语义服从「read 前必须可读」第一原则。"""
        if user_id not in self._uids:
            return FlushStats(success=True, latency_ms=0.0)
        t0 = time.perf_counter()
        user = self._user(user_id)
        user.flush(sync=True)
        return FlushStats(success=True, latency_ms=(time.perf_counter() - t0) * 1000)

    @track_metrics(method="read")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        """读取用户画像状态（profile 条目 + 相关事件），包装成 Memory。

        画像中心范式：read ≠ 检索 top-k turn，而是读「当前画像 + query 相关事件」。
        profile 是 LLM merge 派生物——溯源 date-level（wrapped declared-weak）。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        opts = opts or ReadOpts()
        t0 = time.perf_counter()
        if user_id not in self._uids:
            return ReadResult(memories=[], latency_ms=(time.perf_counter() - t0) * 1000)
        user = self._user(user_id)
        max_tok = int(self._config.get("max_token_size", 1500))
        # profile(chats=[query])：LLM 按当前问题相关性过滤画像
        profiles = user.profile(
            max_token_size=max_tok,
            chats=[{"role": "user", "content": query}],
        )
        memories: list[Memory] = []
        for rank, p in enumerate(profiles[: opts.top_k]):
            content = getattr(p, "content", "") or ""
            if not content:
                continue
            topic = getattr(p, "topic", "")
            sub = getattr(p, "sub_topic", "")
            memories.append(Memory(
                memory_id=str(getattr(p, "id", f"profile_{rank}")),
                text=f"{topic}/{sub}: {content}" if topic else content,
                score=1.0 - rank * 0.05,  # profile 无显式分数，按相关性排序位次
                source_turn_ids=[],  # 画像派生物无法唯一绑定 turn（date-level only）
                timestamp=None,
                metadata={"topic": topic, "sub_topic": sub, "kind": "profile"},
                provenance_mode="wrapped",  # date-level，不能唯一绑定（如实标）
                text_nature="extracted",  # LLM 抽取的画像，非原文
            ))
        return ReadResult(
            memories=memories,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,
            raw={"n_profiles": len(profiles)},
        )

    @track_metrics(method="clear")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def clear(self, user_id: str) -> ClearStats:
        """delete_user：物理删除该 user 全部画像/事件/blob（实测删后读取报错）。"""
        t0 = time.perf_counter()
        n_seen = len(self._seen.pop(user_id, set()))
        uid = self._uids.pop(user_id, None)
        if uid is not None:
            self._ensure_client().delete_user(uid)
        return ClearStats(
            success=True, latency_ms=(time.perf_counter() - t0) * 1000,
            deleted_count=n_seen,
        )

    def close(self) -> None:
        client = self._client
        if client is not None:
            for uid in list(self._uids.values()):
                try:
                    client.delete_user(uid)
                except Exception:
                    pass
            self._uids.clear()

    # ------------------------------------------------------------ 白盒接口

    def get_dependencies(self) -> list:
        from ..contract import Dependency

        return [
            Dependency(
                "memobase-server", "service", required=True,
                check_hint="bash scripts/setup_memobase_server.sh（pg0+redis+源码 uvicorn，8019）",
                swap_supported=False,
            ),
            Dependency(
                self._config.get("model", "deepseek-v4-flash"), "internal_llm", required=False,
                check_hint="config.yaml best_llm_model（画像抽取用）",
                swap_supported=True, config_key="model",
            ),
            Dependency(
                "bge-small-zh-v1.5(shim)", "embedding", required=True,
                check_hint="config.yaml embedding 指向本地 shim 18230（event 检索必需）",
                swap_supported=True, config_key="embedding",
            ),
        ]

    def get_stats(self, user_id: str) -> dict[str, Any]:
        from ..middleware import METRICS

        return {
            "active_users": len(self._uids),
            "seen_turns": len(self._seen.get(user_id, set())),
            **self._stats,
            "metrics": METRICS.snapshot(self.name, user_id),
        }

    def get_injected_providers(self) -> dict[str, str]:
        return {
            "internal_llm": self._config.get("model", "deepseek-v4-flash"),
            "internal_embed": "bge-small-zh-v1.5",
        }

    def get_capability_profile(self):
        from ..contract import STANDARD_FEATURES, Capability, CapabilityProfile

        caps_map = {
            "physical_clear": ("native", "delete_user 物理删（实测删后读取报错），(user_id,project_id) 复合外键隔离"),
            "turn_id_traceback": ("wrapped", "⚠️ date-level：画像是 LLM merge 派生物，同日多 turn 不能唯一绑定（如实声明）"),
            "cognitive_type_filter": ("declared", "profile(semantic)+event(episodic) 双层，但无认知类型过滤 API"),
            "score_normalized": ("computed", "profile 无显式分数，按 LLM 相关性排序位次归一化"),
            "concurrent_safe": ("native", "每 user 独立 memobase user，(user_id,project_id) 复合隔离"),
            "cost_accounting": ("declared", "LLM usage 进 Billing 表/端点，无 per-call API 暴露"),
            "embedding_export": ("declared", "event embedding 内部存储，无导出 API"),
            "flush_blocking": ("native", "flush(sync=True) 同步等画像抽取完成（实测 ~14s）"),
            "consolidate_explicit": ("declared", "画像抽取即在 flush；无独立 consolidate 入口"),
            "batch_write_native": ("wrapped", "ChatBlob.messages 可批量；本 adapter 逐条保溯源"),
            "write_semantic_sync": ("wrapped", "insert 入 buffer，画像 read 前必须 flush(sync)——非 write 同步"),
            "lineage_after_consolidate": ("wrapped", "画像 merge 后仅 date-level 溯源（同 turn_id_traceback）"),
        }
        caps = [
            Capability(feature=f, level=level, note=note)  # type: ignore[arg-type]
            for f in STANDARD_FEATURES
            for level, note in [caps_map[f]]
        ]
        return CapabilityProfile(
            adapter_name=self.name,
            capabilities=caps,
            lane_compatibility={"A1": "compatible", "A2": "compatible",
                                "A3": "compatible", "B": "compatible", "C": "incompatible"},
            lane_notes={
                "C": "画像抽取强依赖 LLM（write→flush 必调），C lane（无 LLM）不兼容",
                "write_semantic_sync": "画像在 flush(sync) 才就位，harness 必须 write→flush→read",
            },
        )
