"""ReMe（agentscope-ai/ReMe 0.3.1.10）的 KidsBench 契约适配。

范式定位：vector 存储 + **agentic 读取**（retrieve 是 LLM 工具循环，~20s/次，
晚绑定变体——与 hindsight-reflect 构成晚绑定家族内对照，机制不同：
工具循环多步检索 vs 一次性合成）。

接入要点（全部来自 Phase 0 实测，见 docs/REME_VERIFIED_FACTS.md）：
1. 中文 patch：vector 路径记忆 prompt 无 _zh 版，默认抽英文记忆——
   构造时单点 patch PromptHandler.prompt_format 追加中文输出指令（幂等）
2. write 缓存 / flush 才真写：ReMe 的 summarize_memory 吃完整对话批，
   逐 turn 调用既贵又割裂语义。write 只进 buffer，flush 时一次 summarize
3. 溯源双路：summarize 自动抽取的记忆带 message_time（LLM 从对话时间标注抄）
   → adapter 维护 ts→turn_id 映射精确反查（wrapped）；LLM 写错时间时
   fallback 节点自带 vector 字段做 cosine 辅路（computed）
4. ref_memory_id：显式 add_memory 路径（Oracle 注入等）用它存 turn_id
5. token 计量：ReMe 不上报 usage（实测），cost_token=0 如实（榜单已标注）
6. agentscope 必须钉 1.0.20（新版缺 agentscope.token，import 即炸）
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..contract import (
    AdapterError,
    ClearStats,
    ConsolidateStats,
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
    "httpx.ConnectTimeout": TimeoutError_,
    "httpx.ConnectError": NetworkError,
    "httpx.TransportError": NetworkError,
    "openai.RateLimitError": RateLimitError,
    "openai.AuthenticationError": AuthError,
}

# 中文输出指令（Phase 0 实测：注入后记忆完全中文化，实体精确）
_ZH_DIRECTIVE = (
    "\n\nIMPORTANT: This is a Chinese-language application. "
    "Always write memory_content, when_to_use, and all profile content "
    "in Chinese (simplified), matching the conversation language. "
    "Keep names and entities exactly as they appear in the conversation."
)

_TS_FMT = "%Y-%m-%d %H:%M:%S"


def install_zh_prompt_patch() -> None:
    """给 ReMe 的 PromptHandler 注入中文输出指令（幂等）。

    根因：ReMe vector 路径的记忆 prompt 只有英文版（File 路径才有 _zh），
    全英文指令驱动 LLM 输出英文记忆——中文 query 检索英文记忆双输。
    长期正解是给上游提 PR 补 _zh prompt（其 PromptHandler 语言后缀机制现成），
    本 patch 是零 fork 的运行时适配。
    """
    from reme.core.prompt_handler import PromptHandler

    if getattr(PromptHandler, "_kidsbench_zh_patched", False):
        return
    original = PromptHandler.prompt_format

    def patched(self: Any, prompt_name: str, **kwargs: Any) -> Any:
        out = original(self, prompt_name, **kwargs)
        if isinstance(out, str):
            return out + _ZH_DIRECTIVE
        return out

    PromptHandler.prompt_format = patched
    PromptHandler._kidsbench_zh_patched = True


class RemeAdapter(MemoryAdapter):
    """Translate ReMe APIs into KidsBench adapter contract."""

    name = "reme"
    paradigm_tags = {
        "representation": "vector+profile",
        "retrieval": "agentic_tool_loop",
        "write_policy": "batch_summarize",
        "controller": "llm_agent",
        "cognitive": ["semantic", "episodic"],
    }

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        client: Any | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        """config 键（client 为 None 时用于构造真实 ReMe）：
        llm: {model, base_url, api_key} / embedding: {model, base_url, api_key, dimensions}
        working_dir: 本地存储目录（默认 /tmp/kidsbench_reme）
        """
        self._config = dict(config or {})
        self._logger = logger or StructuredLogger(self.name)
        self._client = client
        self._started = False
        # write 缓存：user_id → list[Turn]（flush 时一次 summarize）
        self._buffers: dict[str, list[Turn]] = {}
        # 溯源主路映射：user_id → {message_time 字符串: turn_id}
        self._ts_index: dict[str, dict[str, str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ client

    def _ensure_client(self) -> Any:
        if self._client is None:
            install_zh_prompt_patch()
            try:
                from reme import ReMe
            except Exception as err:  # pragma: no cover - 依赖检查
                raise AdapterError(
                    "reme-ai is not installed; run: pip install reme-ai agentscope==1.0.20"
                ) from err
            llm = self._config.get("llm", {})
            emb = self._config.get("embedding", {})
            self._client = ReMe(
                working_dir=self._config.get("working_dir", "/tmp/kidsbench_reme"),
                enable_logo=False,
                log_to_console=False,
                default_llm_config={
                    "backend": "openai",
                    "model_name": llm.get("model", ""),
                    "api_key": llm.get("api_key", ""),
                    "base_url": llm.get("base_url", ""),
                },
                default_embedding_model_config={
                    "backend": "openai",
                    "model_name": emb.get("model", ""),
                    "api_key": emb.get("api_key", ""),
                    "base_url": emb.get("base_url", ""),
                    "dimensions": emb.get("dimensions", 512),
                },
                default_vector_store_config={"backend": "local"},
                # codex 对抗审必修 #3：profile 是文件系统存储，delete_all
                # 清不到，跨题残留会混进 agentic answer——评测关掉
                enable_profile=False,
            )
        if not self._started:
            start = getattr(self._client, "start", None)
            if callable(start):
                self._run(start())
            self._started = True
        return self._client

    # ------------------------------------------------------------ contract

    @track_metrics(method="write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        """write 只进缓存（不调 LLM）——ReMe 的语义单位是对话批，
        flush 时一次 summarize。时间戳进溯源映射表。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.perf_counter()
        self._buffers.setdefault(user_id, []).append(turn)
        ts_str = self._format_ts(turn.timestamp)
        self._ts_index.setdefault(user_id, {})[ts_str] = turn.turn_id
        return WriteStats(
            success=True,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,
            raw={"buffered": len(self._buffers[user_id])},
        )

    @track_metrics(method="flush")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def flush(self, user_id: str) -> FlushStats:
        """真正的写入点：缓存批 → summarize_memory（LLM 多轮工具循环抽取）。

        token 开销发生在这里但 ReMe 不上报 usage（Phase 0 实测）——
        FlushStats 无 cost 字段，榜单 token_note 已标注「未上报」。
        """
        t0 = time.perf_counter()
        turns = self._buffers.get(user_id, [])
        if turns:
            client = self._ensure_client()
            messages = [
                {
                    "role": t.role if t.role in ("user", "assistant") else "user",
                    "content": t.text,
                    "time_created": self._format_ts(t.timestamp),
                }
                for t in turns
            ]
            self._run(client.summarize_memory(messages=messages, user_name=user_id))
            self._buffers[user_id] = []
        return FlushStats(success=True, latency_ms=(time.perf_counter() - t0) * 1000)

    @track_metrics(method="read")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        """agentic 读取：retrieve_memory（LLM 工具循环）→
        合成 answer 作首条 Memory（synthesized）+ retrieved_nodes 原始节点（extracted）。
        """
        if not user_id:
            raise AdapterError("user_id must not be empty")
        opts = opts or ReadOpts()
        t0 = time.perf_counter()
        client = self._ensure_client()
        result = self._run(
            client.retrieve_memory(
                query=query,
                user_name=user_id,
                retrieve_top_k=max(opts.top_k, 5),
                return_dict=True,
            )
        )
        result = result if isinstance(result, dict) else {}
        memories: list[Memory] = []

        nodes = result.get("retrieved_nodes") or []
        node_memories = [
            m for m in (self._node_to_memory(user_id, n) for n in nodes[: opts.top_k])
            if m is not None
        ]

        answer = result.get("answer")
        if isinstance(answer, str) and answer.strip():
            # 合成答案的溯源 = 其引用的全部节点 turn_id 并集
            synth_sources = sorted({
                tid for m in node_memories for tid in m.source_turn_ids
            })
            memories.append(Memory(
                memory_id=f"reme_synth_{user_id}",
                text=answer.strip(),
                score=1.0,
                source_turn_ids=synth_sources,
                timestamp=None,
                metadata={"kind": "agentic_answer"},
                provenance_mode="computed" if synth_sources else "unknown",
                text_nature="synthesized",
            ))
        memories.extend(node_memories)

        return ReadResult(
            memories=memories,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,  # ReMe 不上报 usage（实测），如实计 0
            raw={"n_nodes": len(nodes)},
        )

    @track_metrics(method="clear")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def clear(self, user_id: str) -> ClearStats:
        """物理清场：delete_all（全库，评测每题串行无碰撞）+ 缓存/映射清。"""
        t0 = time.perf_counter()
        # codex 对抗审必修 #2：clear 必须强制启动后物理清——
        # 首题 client 未启动时跳过 delete_all，会让上一进程残留污染首题
        client = self._ensure_client()
        self._run(client.delete_all())
        deleted = 1  # ReMe delete_all 不返回计数，标记执行过
        self._buffers.pop(user_id, None)
        self._ts_index.pop(user_id, None)
        return ClearStats(
            success=True,
            latency_ms=(time.perf_counter() - t0) * 1000,
            deleted_count=deleted,
        )

    @track_metrics(method="consolidate")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def consolidate(self, user_id: str) -> ConsolidateStats:
        """ReMe 的语义整理已在 flush 的 summarize 中完成，无独立 consolidate API。"""
        return ConsolidateStats(success=True, latency_ms=0.0)

    def close(self) -> None:
        if self._client is not None and self._started:
            closer = getattr(self._client, "close", None)
            if callable(closer):
                try:
                    self._run(closer())
                except Exception:
                    self._logger.warning("reme close failed (ignored on teardown)")
            self._started = False

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _format_ts(ts: float | None) -> str:
        dt = datetime.fromtimestamp(float(ts or 0), tz=timezone.utc).astimezone()
        return dt.strftime(_TS_FMT)

    def _node_to_memory(self, user_id: str, node: Any) -> Memory | None:
        """ReMe 节点 → 契约 Memory。溯源双路：
        主路 ref_memory_id（显式写入时存的 turn_id）→ wrapped
        次路 message_time 反查映射表（summarize 自动抽取）→ wrapped
        辅路 节点自带 vector → computed（harness cosine 反查）
        """
        d = node if isinstance(node, dict) else getattr(node, "__dict__", None)
        if not isinstance(d, dict):
            return None
        text = str(d.get("content") or "").strip()
        if not text:
            return None

        source_ids: list[str] = []
        provenance = "unknown"
        ref = str(d.get("ref_memory_id") or "").strip()
        if ref and ref in {t for ts in self._ts_index.get(user_id, {}).values() for t in [ts]}:
            source_ids = [ref]
            provenance = "wrapped"
        if not source_ids:
            ts_str = str(d.get("message_time") or "").strip()
            mapped = self._ts_index.get(user_id, {}).get(ts_str)
            if mapped:
                source_ids = [mapped]
                provenance = "wrapped"

        vector = d.get("vector")
        embedding = list(vector) if isinstance(vector, (list, tuple)) and vector else None
        if not source_ids and embedding:
            provenance = "computed"

        raw_score = d.get("score")
        try:
            score = min(max(float(raw_score), 0.0), 1.0)
        except (TypeError, ValueError):
            score = 0.0

        return Memory(
            memory_id=str(d.get("memory_id") or ""),
            text=text,
            score=score,
            source_turn_ids=source_ids,
            source_embedding=embedding,
            timestamp=None,
            metadata={"memory_type": d.get("memory_type", "")},
            provenance_mode=provenance,
            text_nature="extracted",
        )

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """持久后台 event loop（codex 对抗审必修 #1）。

        ReMe 内部持有 loop 绑定资源（httpx AsyncClient 连接池）——每次调用
        新开 loop 会触发 attached-to-different-loop / closed-loop 类故障。
        全部 async 调用 run_coroutine_threadsafe 投递到同一常驻 loop，
        异常原样传播（旧实现子线程异常被吞、flush 静默假成功）。
        """
        if self._loop is None or self._loop.is_closed():
            loop = asyncio.new_event_loop()

            def runner() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            t = threading.Thread(target=runner, daemon=True, name="reme-loop")
            t.start()
            self._loop = loop
        return self._loop

    def _run(self, result: Any) -> Any:
        """同步桥接：投递到持久 loop，异常原样抛出。"""
        if not inspect.isawaitable(result):
            return result
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(result, loop)
        return future.result(timeout=600)

    # ------------------------------------------------------------ 白盒接口

    def get_dependencies(self) -> list:
        from ..contract import Dependency

        llm = self._config.get("llm", {})
        emb = self._config.get("embedding", {})
        return [
            Dependency(
                "reme-ai", "library", required=True,
                check_hint="pip install reme-ai agentscope==1.0.20（agentscope 必须钉版本）",
                swap_supported=False,
            ),
            Dependency(
                llm.get("model", "unknown"), "internal_llm", required=True,
                check_hint="default_llm_config 三件套统一注入",
                swap_supported=True, config_key="llm",
                actual_model=llm.get("model"),
            ),
            Dependency(
                emb.get("model", "unknown"), "embedding", required=True,
                check_hint="仅支持 OpenAI 兼容 API 形态——本地模型经 embedding_shim 对齐",
                swap_supported=True, config_key="embedding",
                actual_model=emb.get("model"),
            ),
        ]

    def get_stats(self, user_id: str) -> dict[str, Any]:
        from ..middleware import METRICS

        return {
            "buffered_turns": len(self._buffers.get(user_id, [])),
            "ts_index_size": len(self._ts_index.get(user_id, {})),
            "metrics": METRICS.snapshot(self.name, user_id),
        }

    def get_capability_profile(self):
        from ..contract import STANDARD_FEATURES, Capability, CapabilityProfile

        caps_map = {
            "physical_clear": ("native", "delete_all 全库清（实测归零）；无按 user 删，评测串行无碰撞"),
            "turn_id_traceback": ("wrapped", "message_time→turn_id 映射反查（smoke 实测 12/12 命中）；节点 vector 已填 source_embedding 但 harness 未实装 cosine 反查——主路失配时该题归因归零（如实声明）"),
            "cognitive_type_filter": ("declared", "memory_type personal/task/tool 三类，非认知类型"),
            "score_normalized": ("native", "节点 score 为相似度，已在 [0,1]（实测 0.47）"),
            "concurrent_safe": ("declared", "user_name 逻辑隔离，vector store 共享（delete_all 全清）"),
            "cost_accounting": ("declared", "return_dict 无 usage 字段（实测），token 未上报"),
            "embedding_export": ("native", "节点自带 vector 字段（实测回传）"),
            "flush_blocking": ("native", "flush 同步 summarize，返回即可召回"),
            "consolidate_explicit": ("declared", "无独立 consolidate API，语义整理在 summarize 内"),
            "batch_write_native": ("native", "summarize 原生吃对话批（write 缓存 flush 批写）"),
            "write_semantic_sync": ("native", "summarize 同步完成抽取"),
            "lineage_after_consolidate": ("declared", "summarize 抽取记忆靠 message_time 关联，LLM 写错时间则降辅路"),
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
                                "A3": "degraded", "B": "compatible", "C": "incompatible"},
            lane_notes={
                "A3": "agentic retrieve 的多轮工具调用对小模型格式稳定性未验证",
                "C": "读取是 LLM 工具循环（agentic），无纯检索模式",
            },
        )
