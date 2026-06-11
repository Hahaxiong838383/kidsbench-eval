"""Hindsight (vectorize-io) adapter：recall/reflect 双模式 = 早/晚绑定范式旋钮。

接入事实依据 docs/HINDSIGHT_VERIFIED_FACTS.md（Phase 0 十点全绿实测）：
- retain 默认同步（返回前 LLM 抽取完成）→ flush 轻量
- recall 不调 LLM（四路检索+rerank）；reflect 调 LLM 合成（usage 暴露）
- metadata 完整透传 → recall 模式溯源走 wrapped；ReflectFact 无 metadata
  → reflect 模式溯源走 source_embedding 辅路（computed，合成范式的真实溯源能力）
- retain 非幂等（同内容写两次=两条）→ 本 adapter 写前 sidecar 查重
- 评测标准 env：中文 embedding/reranker + 关 auto-consolidation（由 harness/server 侧设置）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..contract import (
    STANDARD_FEATURES,
    AdapterError,
    Capability,
    CapabilityProfile,
    ClearStats,
    ConsolidateStats,
    Dependency,
    FlushStats,
    Memory,
    MemoryAdapter,
    ReadOpts,
    ReadResult,
    Turn,
    WriteStats,
)
from ..middleware import (
    METRICS,
    AuthError,
    EmbeddingService,
    NetworkError,
    RateLimitError,
    SidecarStore,
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
    "httpx.HTTPStatusError": NetworkError,
    "openai.RateLimitError": RateLimitError,
    "openai.AuthenticationError": AuthError,
}

_VALID_MODES = ("recall", "reflect")

# Hindsight fact_type → 契约 cognitive 受控词表
_FACT_TYPE_TO_COGNITIVE = {
    "world": "semantic",
    "experience": "episodic",
    "opinion": "semantic",
    "observation": "semantic",
}


class _NoopEmbeddingService(EmbeddingService):
    """测试/默认模式的兜底 embedding。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def dim(self) -> int:
        return 1


class HindsightAdapter(MemoryAdapter):
    """Translate Hindsight client APIs into KidsBench adapter contract.

    mode="recall"  → read 走 client.recall（不调 LLM，read 成本≈0，早绑定式廉价检索点）
    mode="reflect" → read 走 client.reflect（调 LLM 合成，read 计 token，晚绑定合成点）
    两个身份的 bank 物理隔离（bank 后缀），防止 reflect 固化产物被 recall 白嫖。
    """

    paradigm_tags = {
        "representation": "vector+entity",
        "retrieval": "hybrid_rerank",
        "write_policy": "reactive",
        "controller": "rule",
        "cognitive": ["episodic", "semantic"],
    }

    def __init__(
        self,
        *,
        mode: str = "recall",
        config: dict[str, Any] | None = None,
        client: Any = None,
        sidecar: SidecarStore | None = None,
        embedding_service: EmbeddingService | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        if mode not in _VALID_MODES:
            raise AdapterError(f"mode must be one of {_VALID_MODES}, got {mode!r}")
        self._mode = mode
        self.name = f"hindsight-{mode}"
        self._config = dict(config or {})
        self._sidecar = sidecar or SidecarStore(backend="memory")
        self._embedding_service = embedding_service or _NoopEmbeddingService()
        self._logger = logger or StructuredLogger(self.name)

        self._llm_model_name = str(self._config.get("injected_llm_model", "unknown"))
        self._embed_model_name = str(self._config.get("injected_embed_model", "unknown"))

        self.client = client if client is not None else self._create_client(self._config)

    # ---- 构造 ----

    @staticmethod
    def _create_client(config: dict[str, Any]) -> Any:
        base_url = config.get("base_url")
        if not base_url:
            raise AdapterError(
                "hindsight 需要 config['base_url']（外部已起的 HindsightServer 地址），"
                "或测试时直接注入 client"
            )
        try:
            from hindsight_client import Hindsight
        except Exception as err:  # pragma: no cover - 依赖检查覆盖
            raise AdapterError(
                "hindsight-client 未安装；用 .venv-hindsight 运行"
            ) from err
        return Hindsight(base_url=base_url)

    def _bank(self, user_id: str) -> str:
        """双身份 bank 物理隔离：{user_id}__{mode}。"""
        return f"{user_id}__{self._mode}"

    @staticmethod
    def _as_dict(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump") and callable(item.model_dump):
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return dumped
        if hasattr(item, "__dict__"):
            return dict(item.__dict__)
        return {}

    @staticmethod
    def _usage_tokens(payload: dict[str, Any]) -> int:
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            usage = HindsightAdapter._as_dict(usage)
        total = usage.get("total_tokens")
        if isinstance(total, (int, float)):
            return int(total)
        return 0

    # ---- 七方法 ----

    @track_metrics(method="write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")

        # 幂等查重：retain 非幂等（实测同内容写两次=两条），retry 中间件重试会复制记忆
        if self._sidecar.get_memory_ids(user_id, turn.turn_id):
            self._logger.info("hindsight_write_dedup", user_id=user_id, turn_id=turn.turn_id)
            return WriteStats(success=True, raw={"deduplicated": True})

        result = self.client.retain(
            bank_id=self._bank(user_id),
            content=turn.text,
            timestamp=datetime.fromtimestamp(turn.timestamp, tz=timezone.utc),
            # metadata 值必须 str（client 签名 dict[str, str]）
            metadata={
                "turn_id": turn.turn_id,
                "session_id": turn.session_id,
                "role": turn.role,
            },
        )
        rd = self._as_dict(result)
        cost = self._usage_tokens(rd)
        # RetainResponse 不返回 memory ids → sidecar 只记「该 turn 已写入」哨兵（幂等用）
        self._sidecar.put(user_id, turn.turn_id, [f"retained:{turn.turn_id}"])

        self._logger.info(
            "hindsight_write",
            user_id=user_id,
            turn_id=turn.turn_id,
            items_count=rd.get("items_count"),
            cost_token=cost,
        )
        return WriteStats(success=True, cost_token=cost, raw={"items_count": rd.get("items_count")})

    @track_metrics(method="read")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        read_opts = opts or ReadOpts()
        if self._mode == "recall":
            return self._read_recall(user_id, query, read_opts)
        return self._read_reflect(user_id, query, read_opts)

    def _query_timestamp(self, read_opts: ReadOpts) -> str | None:
        if read_opts.current_timestamp is None:
            return None
        return datetime.fromtimestamp(read_opts.current_timestamp, tz=timezone.utc).isoformat()

    def _read_recall(self, user_id: str, query: str, read_opts: ReadOpts) -> ReadResult:
        resp = self.client.recall(
            bank_id=self._bank(user_id),
            query=query,
            query_timestamp=self._query_timestamp(read_opts),
        )
        rd = self._as_dict(resp)
        records = rd.get("results") or []

        memories: list[Memory] = []
        for rank, item in enumerate(records[: read_opts.top_k]):
            data = self._as_dict(item)
            text = str(data.get("text") or "")
            metadata = data.get("metadata") or {}
            turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
            source_turn_ids = [turn_id] if isinstance(turn_id, str) and turn_id else []
            source_embedding = None
            if read_opts.include_provenance and text:
                source_embedding = self._embedding_service.embed([text])[0]
            memories.append(
                Memory(
                    memory_id=str(data.get("id") or f"hs_{rank}"),
                    text=text,
                    # recall 无显式 score → 排名归一化（首位 1.0 递减），诚实标 computed
                    score=max(0.0, 1.0 - rank * (1.0 / max(len(records), 1))),
                    source_turn_ids=source_turn_ids,
                    source_embedding=source_embedding,
                    timestamp=None,
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                    provenance_mode="wrapped" if source_turn_ids else "computed",
                    text_nature="extracted",
                )
            )
        # recall 不调 LLM：read 成本如实为 0（范式数据点）
        return ReadResult(memories=memories, cost_token=0, raw={"result_count": len(records)})

    def _read_reflect(self, user_id: str, query: str, read_opts: ReadOpts) -> ReadResult:
        resp = self.client.reflect(
            bank_id=self._bank(user_id),
            query=query,
            include_facts=True,
        )
        rd = self._as_dict(resp)
        cost = self._usage_tokens(rd)
        synthesis = str(rd.get("text") or "")
        facts = rd.get("based_on") or []
        if not isinstance(facts, list):
            # reflect agent 部分失败时 based_on 可能非 list（实测 smoke 炸过切片）
            facts = list(facts.values()) if isinstance(facts, dict) else []

        memories: list[Memory] = []
        # 晚绑定核心产出：synthesis 作为首条 Memory（text_nature=synthesized）
        if synthesis:
            memories.append(
                Memory(
                    memory_id="hs_reflect_synthesis",
                    text=synthesis,
                    score=1.0,
                    source_turn_ids=[],  # 合成内容无精确 turn 映射（范式真实溯源能力）
                    source_embedding=(
                        self._embedding_service.embed([synthesis])[0]
                        if read_opts.include_provenance
                        else None
                    ),
                    metadata={"kind": "synthesis"},
                    provenance_mode="computed",
                    text_nature="synthesized",
                )
            )
        for rank, item in enumerate(facts[: read_opts.top_k]):
            data = self._as_dict(item)
            text = str(data.get("text") or "")
            memories.append(
                Memory(
                    memory_id=str(data.get("id") or f"hs_fact_{rank}"),
                    text=text,
                    score=max(0.0, 0.9 - rank * (0.9 / max(len(facts), 1))),
                    # ReflectFact 无 metadata → 辅路 cosine 反查（computed）
                    source_turn_ids=[],
                    source_embedding=(
                        self._embedding_service.embed([text])[0]
                        if read_opts.include_provenance and text
                        else None
                    ),
                    metadata={"fact_type": str(data.get("type") or "unknown")},
                    provenance_mode="computed",
                    text_nature="extracted",
                )
            )
        return ReadResult(memories=memories, cost_token=cost, raw={"fact_count": len(facts)})

    @track_metrics(method="clear")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def clear(self, user_id: str) -> ClearStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        self.client.delete_bank(bank_id=self._bank(user_id))
        deleted = self._sidecar.clear_user(user_id)
        self._logger.info("hindsight_clear", user_id=user_id, sidecar_deleted=deleted)
        return ClearStats(success=True, deleted_count=deleted)

    @track_metrics(method="flush")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def flush(self, user_id: str) -> FlushStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        # Phase 0 实测：retain 默认同步（返回前抽取完成且立即可召回）→ flush 轻量
        return FlushStats(success=True, latency_ms=0.0)

    @track_metrics(method="consolidate")
    def consolidate(self, user_id: str) -> ConsolidateStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        if self._mode == "recall":
            # recall 身份定位「廉价检索点」：consolidate 禁调 LLM，防成本归属混淆（评审收敛1）
            return ConsolidateStats(
                success=True, latency_ms=0.0, consolidated_count=0,
                consolidation_phase="explicit",
            )
        result = self._trigger_consolidation(user_id)
        rd = self._as_dict(result)
        return ConsolidateStats(
            success=True,
            cost_token=self._usage_tokens(rd),
            consolidated_count=int(rd.get("consolidated_count") or 0),
            consolidation_phase="explicit",
        )

    def _trigger_consolidation(self, user_id: str) -> Any:
        """显式触发 consolidation（POST /v1/default/banks/{bank}/consolidate）。"""
        trigger = getattr(self.client, "trigger_consolidation", None)
        if callable(trigger):
            return trigger(bank_id=self._bank(user_id))
        # hindsight_client 0.8.1 无封装方法 → httpx 直调
        import httpx

        base_url = self._config.get("base_url", "")
        resp = httpx.post(
            f"{base_url}/v1/default/banks/{self._bank(user_id)}/consolidate",
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()

    def get_dependencies(self) -> list[Dependency]:
        return [
            Dependency(
                "hindsight-server",
                "service",
                required=True,
                check_hint="HindsightServer(db_url='pg0') embedded 或外部 base_url",
                swap_supported=False,
            ),
            Dependency(
                self._llm_model_name,
                "internal_llm",
                required=True,
                check_hint="HindsightServer(llm_base_url=...) 统一注入",
                swap_supported=True,
                config_key="llm_base_url",
                actual_model=self._llm_model_name if self._llm_model_name != "unknown" else None,
            ),
            Dependency(
                self._embed_model_name,
                "internal_embed",
                required=True,
                check_hint="env HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL",
                swap_supported=True,
                config_key="embeddings_local_model",
                actual_model=self._embed_model_name if self._embed_model_name != "unknown" else None,
            ),
            Dependency(
                "BAAI/bge-reranker-v2-m3",
                "model",
                required=True,
                check_hint="env HINDSIGHT_API_RERANKER_LOCAL_MODEL（中文必换，英文默认会雪崩）",
                swap_supported=True,
            ),
        ]

    def get_injected_providers(self) -> dict[str, str]:
        return {"internal_llm": self._llm_model_name, "internal_embed": self._embed_model_name}

    def get_stats(self, user_id: str) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "sidecar": self._sidecar.stats(user_id),
            "metrics": METRICS.snapshot(self.name, user_id),
        }

    def get_capability_profile(self) -> CapabilityProfile:
        is_reflect = self._mode == "reflect"
        caps_map = {
            "physical_clear": ("native", "delete_bank 六步级联 + DROP per-bank HNSW（源码+实测）"),
            "turn_id_traceback": (
                ("computed", "reflect facts 无 metadata，辅路 cosine 反查")
                if is_reflect
                else ("wrapped", "retain metadata.turn_id 完整透传（实测）")
            ),
            "cognitive_type_filter": ("wrapped", "fact_type 4 类（world/experience/opinion/observation）LLM 抽取产物"),
            "score_normalized": ("computed", "recall 无显式分数，排名归一化"),
            "concurrent_safe": ("native", "bank_id 物理隔离（实测交叉删除不互伤）"),
            "cost_accounting": ("native", "retain/reflect usage 原生返回（实测）"),
            "embedding_export": ("computed", "EmbeddingService 统一空间重 embed"),
            "flush_blocking": ("native", "retain 默认同步，返回即可召回（实测）"),
            "consolidate_explicit": (
                ("native", "显式 POST /consolidate；auto-consolidation 已关")
                if is_reflect
                else ("declared", "recall 身份禁 consolidate（防成本归属混淆）")
            ),
            "batch_write_native": ("declared", "保 1:1 溯源，循环 write（retain_batch 牺牲 metadata 粒度）"),
            "write_semantic_sync": ("native", "retain 同步抽取，写完立即可读（实测 20/20）"),
            "lineage_after_consolidate": ("declared", "observation 不带原 turn metadata（实测英文 observation 无 turn_id）"),
        }
        caps = [
            Capability(feature=feature, level=level, note=note)  # type: ignore[arg-type]
            for feature in STANDARD_FEATURES
            for level, note in [caps_map[feature]]
        ]
        return CapabilityProfile(
            adapter_name=self.name,
            capabilities=caps,
            lane_compatibility={
                "A1": "compatible",
                "A2": "compatible",
                "A3": "degraded",
                "B": "compatible",
                "C": "compatible" if not is_reflect else "incompatible",
            },
            lane_notes={
                "A3": "本地 7B 做 fact 抽取格式稳定性未验证",
                "C": (
                    "reflect 模式强依赖 LLM 合成，无法纯检索"
                    if is_reflect
                    else "recall 路径不调 LLM，但 write(retain) 仍需 LLM 抽取"
                ),
            },
        )

    def close(self) -> None:
        """显式释放 client 连接（防 aiohttp unclosed session 警告）。"""
        closer = getattr(self.client, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # pragma: no cover - teardown 不抛
                pass
