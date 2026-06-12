"""MemMachine（MemMachine/MemMachine）的 KidsBench 契约适配。

范式定位：**真值保存（immutable ledger）**——原始对话 + 句级索引，写入不做 LLM
浓缩压缩，最大限度保留原文。与 mem0/reme 这类「写时 LLM 抽取」的早绑定系统构成
对照组：T3 矛盾更新题它能提供无损上下文，抗「反馈环路腐蚀」。

接入要点（全部来自 Phase 0 实测，见 docs/MEMMACHINE_VERIFIED_FACTS.md）：
1. 部署：全 SQLite 嵌入式（event backend + sqlite_vector_store/usearch），零 docker/
   PG/Neo4j——scripts/setup_memmachine_server.sh 起 server（port 8021）
2. HTTP API v2：write=POST /memories，read=POST /memories/search，clear=POST /projects/delete
3. 溯源 native：MemoryMessage.metadata={"turn_id":...} 写入 → 检索 episode 原样回传 + score
4. 虚拟时钟：MemoryMessage.timestamp（ISO8601）→ episode.created_at 原样落地，排序用它
5. 隔离：每个 user_id 映射独立 project_id（org_id 固定）；clear 删整个 project
6. read 取 STM∪LTM 并集：episode 同时写 STM+LTM，search 回包对仍在 STM 的条目在 LTM
   列表显示空（STM 优先去重），并集取全（带 score）
7. 非幂等：同内容写两次召回两条 → adapter sidecar turn_id 写前查重
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
    "requests.exceptions.Timeout": TimeoutError_,
    "requests.exceptions.ConnectionError": NetworkError,
    "openai.RateLimitError": RateLimitError,
    "openai.AuthenticationError": AuthError,
}

_ORG_ID = "kidsbench"


class MemMachineAdapter(MemoryAdapter):
    """Translate MemMachine HTTP v2 API into KidsBench adapter contract."""

    name = "memmachine"
    paradigm_tags = {
        "representation": "raw_text+sentence_index",
        "retrieval": "vector+bm25_rrf",
        "write_policy": "append_only_verbatim",
        "controller": "rule",
        "cognitive": ["episodic"],
    }

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        session: Any | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        """config 键：
        base_url: MemMachine v2 API（默认 http://127.0.0.1:8021/api/v2）
        timeout: 单请求超时秒（默认 180）
        session: requests.Session（测试可注入 mock）
        """
        self._config = dict(config or {})
        self._base = self._config.get("base_url", "http://127.0.0.1:8021/api/v2").rstrip("/")
        self._timeout = float(self._config.get("timeout", 180))
        self._logger = logger or StructuredLogger(self.name)
        self._session = session
        # user_id → 已写入的 turn_id 集合（幂等查重 sidecar，MemMachine 本身非幂等）
        self._seen: dict[str, set[str]] = {}
        self._stats = {"total_writes": 0, "total_reads": 0, "dedup_hits": 0}

    # ------------------------------------------------------------ client

    def _ensure_session(self) -> Any:
        if self._session is None:
            try:
                import requests
            except Exception as err:  # pragma: no cover - 依赖检查
                raise AdapterError("requests not installed; run: pip install requests") from err
            self._session = requests.Session()
        return self._session

    @staticmethod
    def _project_id(user_id: str) -> str:
        """user_id → project_id（每 user 独立 project 做物理隔离）。
        project_id 校验允许字母数字下划线连字符冒号 + Unicode，user_id 已是安全形态。"""
        return user_id

    def _post(self, path: str, body: dict) -> dict:
        session = self._ensure_session()
        resp = session.post(f"{self._base}{path}", json=body, timeout=self._timeout)
        if resp.status_code >= 300:
            raise AdapterError(f"{path} -> {resp.status_code}: {resp.text[:200]}")
        if not resp.text:
            return {}
        try:
            return resp.json()
        except ValueError as err:
            # 真实 502/HTML 错误页/截断响应：JSON 解析失败也走 fail-fast（不漏成非 AdapterError）
            raise AdapterError(f"{path} 返回非 JSON（{resp.status_code}）：{resp.text[:120]}") from err

    @staticmethod
    def _ts_iso(turn: Turn) -> str:
        """Turn.timestamp（unix float）→ ISO8601（虚拟时钟注入位）。"""
        return datetime.fromtimestamp(turn.timestamp, tz=timezone.utc).isoformat()

    # ------------------------------------------------------------ contract

    @track_metrics(method="write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        """POST /memories，metadata.turn_id + timestamp=虚拟时间。写前查重防重复。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.perf_counter()
        seen = self._seen.setdefault(user_id, set())
        if turn.turn_id in seen:
            # MemMachine 非幂等：同内容写两次召回两条。sidecar 查重拦截重试。
            self._stats["dedup_hits"] += 1
            return WriteStats(
                success=True, latency_ms=(time.perf_counter() - t0) * 1000,
                cost_token=0, raw={"deduplicated": True},
            )
        self._post("/memories", {
            "org_id": _ORG_ID,
            "project_id": self._project_id(user_id),
            "messages": [{
                "content": turn.text,
                "producer": turn.role or "user",
                "role": turn.role or "user",
                "timestamp": self._ts_iso(turn),
                "metadata": {"turn_id": turn.turn_id},
            }],
        })
        seen.add(turn.turn_id)
        self._stats["total_writes"] += 1
        return WriteStats(
            success=True, latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,  # write 含 embedding 调用，MemMachine 不上报 usage
        )

    @track_metrics(method="flush")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def flush(self, user_id: str) -> FlushStats:
        """write 路径同步落库（含 embedding ingest），无需额外 flush。"""
        return FlushStats(success=True, latency_ms=0.0)

    @track_metrics(method="read")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        """POST /memories/search，取 STM∪LTM 并集（STM 优先去重）。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        opts = opts or ReadOpts()
        t0 = time.perf_counter()
        out = self._post("/memories/search", {
            "org_id": _ORG_ID,
            "project_id": self._project_id(user_id),
            "query": query,
            "top_k": max(opts.top_k, 5),
        })
        self._stats["total_reads"] += 1
        episodes = self._merge_episodes(out)
        memories: list[Memory] = []
        for ep in episodes[: opts.top_k]:
            content = ep.get("content", "")
            if not content:
                continue
            turn_id = (ep.get("metadata") or {}).get("turn_id")
            memories.append(Memory(
                memory_id=str(ep.get("uid", "")),
                text=content,
                score=float(ep.get("score", 0.0)),
                source_turn_ids=[turn_id] if turn_id else [],
                timestamp=self._parse_created(ep.get("created_at")),
                metadata={"layer": ep.get("_layer")},
                provenance_mode="native" if turn_id else "unknown",
                text_nature="verbatim",  # 原文保存，非 LLM 抽取
            ))
        return ReadResult(
            memories=memories,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,
            raw={"n_episodes": len(episodes)},
        )

    @track_metrics(method="clear")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def clear(self, user_id: str) -> ClearStats:
        """删整个 project（物理清除，实测删后 search 抛 SessionDeletedError）。"""
        t0 = time.perf_counter()
        n_seen = len(self._seen.pop(user_id, set()))
        self._post("/projects/delete", {
            "org_id": _ORG_ID, "project_id": self._project_id(user_id),
        })
        return ClearStats(
            success=True, latency_ms=(time.perf_counter() - t0) * 1000,
            deleted_count=n_seen,
        )

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _merge_episodes(resp: dict) -> list[dict]:
        """STM∪LTM 并集：LTM 按 score 降序（语义相关性），STM 接其后（最近写入，无 score）。

        MemMachine search 回包对仍在 STM 的条目在 LTM 列表显示空，重启后才入 LTM——
        运行中取并集才能拿全。codex 对抗审 P1：STM 无 score，不能塞 score=0 再全局排序
        （会把最近记忆埋到最底）。改为分层排序：LTM 按 score 排，STM 保留插入序接在后面，
        STM score 标 None 不参与 LTM 的数值比较（语义=相关性优先，STM 是兜底召回）。"""
        em = (resp.get("content") or {}).get("episodic_memory") or {}
        stm = ((em.get("short_term_memory") or {}).get("episodes")) or []
        ltm = ((em.get("long_term_memory") or {}).get("episodes")) or []
        ltm_sorted = sorted(
            ({**ep, "_layer": "ltm"} for ep in ltm),
            key=lambda e: e.get("score", 0.0), reverse=True,
        )
        merged: list[dict] = []
        seen_uids: set[str] = set()
        for ep in [*ltm_sorted, *({**e, "_layer": "stm"} for e in stm)]:
            uid = str(ep.get("uid", ""))
            if uid and uid not in seen_uids:
                seen_uids.add(uid)
                merged.append(ep)
        return merged

    @staticmethod
    def _parse_created(value: Any) -> float | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            return None

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass

    # ------------------------------------------------------------ 白盒接口

    def get_dependencies(self) -> list:
        from ..contract import Dependency

        return [
            Dependency(
                "memmachine-server", "service", required=True,
                check_hint="bash scripts/setup_memmachine_server.sh（全 SQLite，port 8021）",
                swap_supported=False,
            ),
            Dependency(
                self._config.get("model", "deepseek-v4-flash"), "internal_llm", required=False,
                check_hint="cfg.yml language_models.openai_model（短期记忆/检索 agent 用）",
                swap_supported=True, config_key="model",
            ),
            Dependency(
                "bge-small-zh-v1.5(shim)", "embedding", required=True,
                check_hint="cfg.yml embedders 指向本地 shim 18230（512维对齐评测标准）",
                swap_supported=True, config_key="embedding",
            ),
        ]

    def get_stats(self, user_id: str) -> dict[str, Any]:
        from ..middleware import METRICS

        return {
            "active_projects": len(self._seen),
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
            "physical_clear": ("native", "POST /projects/delete 物理删（实测删后 search 抛 SessionDeletedError）"),
            "turn_id_traceback": ("native", "metadata.turn_id 写入→检索 episode 原样回传（实测 t_001）"),
            "cognitive_type_filter": ("declared", "episodic 单一类型；semantic 抽取本 adapter 不启用"),
            "score_normalized": ("computed", "LTM episode 自带 score（实测 0.56）；STM 无 score（兜底召回排其后），非全局归一"),
            "concurrent_safe": ("wrapped", "server 端 project_id 隔离；adapter 共享 Session+_seen 非线程安全（评测串行 OK）"),
            "cost_accounting": ("declared", "embedding usage 走 Prometheus，无 per-call API 暴露"),
            "embedding_export": ("declared", "vector 内部存储，无导出 API"),
            "flush_blocking": ("native", "write 同步落库含 embedding ingest，立即可检索"),
            "consolidate_explicit": ("declared", "真值保存范式无 consolidate；semantic consolidation 已关"),
            "batch_write_native": ("wrapped", "messages[] 可批量；本 adapter 逐条保 1:1 turn_id 溯源"),
            "write_semantic_sync": ("native", "write 返回即可 read（实测）"),
            "lineage_after_consolidate": ("native", "无 consolidate，metadata.turn_id 永不丢"),
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
                                "A3": "compatible", "B": "compatible", "C": "degraded"},
            lane_notes={
                "C": "短期记忆/检索 agent 依赖 LLM；纯检索 C lane 降级（读端仍走向量）",
            },
        )
