"""Mem0 adapter implementation for KidsBench contract."""
from __future__ import annotations

import os
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
    GlobalRateLimiter,
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
    "httpx.TransportError": NetworkError,
    "requests.exceptions.Timeout": TimeoutError_,
    "requests.exceptions.ConnectionError": NetworkError,
    "openai.RateLimitError": RateLimitError,
    "openai.AuthenticationError": AuthError,
    "openai.PermissionDeniedError": AuthError,
    "openai.APIConnectionError": NetworkError,
    "openai.APITimeoutError": TimeoutError_,
    "mem0.errors.RateLimitError": RateLimitError,
    "mem0.errors.AuthenticationError": NetworkError,
    "mem0.errors.APIConnectionError": NetworkError,
}


class _NoopEmbeddingService(EmbeddingService):
    """Fallback embedding service used in tests/default mode."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def dim(self) -> int:
        return 1


class Mem0Adapter(MemoryAdapter):
    """Translate mem0 client APIs into KidsBench adapter contract."""

    name = "mem0"
    paradigm_tags = {
        "representation": "vector+entity",
        "retrieval": "hybrid_rerank",
        "write_policy": "reactive",
        "controller": "rule",
        "cognitive": ["semantic"],
    }

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        sidecar: SidecarStore | None = None,
        embedding_service: EmbeddingService | None = None,
        rate_limiter: GlobalRateLimiter | None = None,
        logger: StructuredLogger | None = None,
        disable_telemetry: bool = True,
    ) -> None:
        self._config = dict(config or {})
        self._sidecar = sidecar or SidecarStore(backend="memory")
        self._embedding_service = embedding_service or _NoopEmbeddingService()
        self._rate_limiter = rate_limiter
        self._logger = logger or StructuredLogger(self.name)

        if disable_telemetry:
            # mem0 telemetry must be disabled for K12 privacy baseline.
            os.environ["MEM0_TELEMETRY"] = "false"

        self._llm_model_name = self._extract_llm_name(self._config)
        self._embed_model_name = self._extract_embed_name(self._config)
        self._batch_write_native = True

        self.client = self._create_client(self._config)

    @staticmethod
    def _extract_llm_name(config: dict[str, Any]) -> str:
        llm = config.get("llm")
        if isinstance(llm, dict):
            model = llm.get("model")
            if isinstance(model, str) and model.strip():
                return model
        return "gpt-4o"

    @staticmethod
    def _extract_embed_name(config: dict[str, Any]) -> str:
        embedding = config.get("embedding")
        if isinstance(embedding, dict):
            model = embedding.get("model")
            if isinstance(model, str) and model.strip():
                return model
        return "text-embedding-ada-002"

    @staticmethod
    def _create_client(config: dict[str, Any]) -> Any:
        try:
            from mem0 import Memory
        except Exception as err:  # pragma: no cover - covered by dependency checks
            raise AdapterError(
                "mem0ai is not installed; run: pip install -e '.[mem0]'"
            ) from err
        return Memory.from_config(config) if config else Memory()

    @track_metrics(method="write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")

        payload = [{"role": turn.role, "content": turn.text}]
        metadata = {
            "turn_id": turn.turn_id,
            "ts": turn.timestamp,
            "session_id": turn.session_id,
        }
        result = self.client.add(messages=payload, user_id=user_id, metadata=metadata)

        memory_ids = self._extract_memory_ids(result)
        self._sidecar.put(user_id, turn.turn_id, memory_ids)

        self._logger.info(
            "mem0_write",
            user_id=user_id,
            turn_id=turn.turn_id,
            memory_ids=memory_ids,
            memory_count=len(memory_ids),
        )
        return WriteStats(success=True, raw={"memory_ids": memory_ids, "result": result})

    @track_metrics(method="batch_write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def batch_write(self, user_id: str, turns: list[Turn]) -> list[WriteStats]:
        if not turns:
            return []
        if not user_id:
            raise AdapterError("user_id must not be empty")

        messages = [{"role": t.role, "content": t.text} for t in turns]
        turn_meta = [
            {
                "turn_id": t.turn_id,
                "ts": t.timestamp,
                "session_id": t.session_id,
            }
            for t in turns
        ]

        # mem0.add supports list messages in a single call, but metadata is request-level.
        # We write one batch marker and preserve per-turn links in sidecar.
        result = self.client.add(
            messages=messages,
            user_id=user_id,
            metadata={
                "batch": True,
                "turn_count": len(turns),
                "turn_ids": [t.turn_id for t in turns],
                "turn_meta": turn_meta,
            },
        )

        memory_ids = self._extract_memory_ids(result)
        if len(memory_ids) == len(turns):
            for turn, memory_id in zip(turns, memory_ids, strict=True):
                self._sidecar.put(user_id, turn.turn_id, [memory_id])
        else:
            for turn in turns:
                self._sidecar.put(user_id, turn.turn_id, memory_ids)

        self._batch_write_native = True
        stats = [WriteStats(success=True, raw={"memory_ids": memory_ids}) for _ in turns]
        self._logger.info(
            "mem0_batch_write",
            user_id=user_id,
            turn_count=len(turns),
            memory_count=len(memory_ids),
        )
        return stats

    @track_metrics(method="read")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        if not user_id:
            raise AdapterError("user_id must not be empty")

        read_opts = opts or ReadOpts()
        items = self.client.search(query=query, user_id=user_id, limit=read_opts.top_k)
        records = self._normalize_records(items)

        memories: list[Memory] = []
        for item in records:
            memory_id = self._extract_memory_id(item)
            text = self._extract_memory_text(item)
            score = self._extract_memory_score(item)
            timestamp = self._extract_timestamp(item)
            metadata = self._extract_metadata(item)

            source_turn_ids: list[str] = []
            source_embedding: list[float] | None = None
            if read_opts.include_provenance:
                source_turn_ids = self._extract_source_turn_ids(user_id=user_id, item=item, memory_id=memory_id)
                source_embedding = self._embedding_service.embed([text])[0] if text else None

            memories.append(
                Memory(
                    memory_id=memory_id,
                    text=text,
                    score=score,
                    source_turn_ids=source_turn_ids,
                    source_embedding=source_embedding,
                    timestamp=timestamp,
                    metadata=metadata,
                )
            )

        return ReadResult(memories=memories, raw={"result_count": len(records)})

    @track_metrics(method="clear")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def clear(self, user_id: str) -> ClearStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        self.client.delete_all(user_id=user_id)
        deleted_count = self._sidecar.clear_user(user_id)
        self._logger.info("mem0_clear", user_id=user_id, deleted_count=deleted_count)
        return ClearStats(success=True, deleted_count=deleted_count)

    @track_metrics(method="flush")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def flush(self, user_id: str) -> FlushStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        get_all = getattr(self.client, "get_all", None)
        if callable(get_all):
            get_all(user_id=user_id, limit=1)
        return FlushStats(success=True, latency_ms=0.0)

    @track_metrics(method="consolidate")
    def consolidate(self, user_id: str) -> ConsolidateStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        return ConsolidateStats(success=True, latency_ms=0.0, consolidated_count=0)

    def get_dependencies(self) -> list[Dependency]:
        return [
            Dependency(
                "mem0ai",
                "model",
                required=True,
                check_hint="pip install mem0ai",
                swap_supported=False,
            ),
            Dependency(
                self._llm_model_name,
                "internal_llm",
                required=True,
                check_hint="config.llm",
                swap_supported=True,
            ),
            Dependency(
                self._embed_model_name,
                "internal_embed",
                required=True,
                check_hint="config.embedding",
                swap_supported=True,
            ),
            Dependency(
                "OPENAI_API_KEY or DASHSCOPE_API_KEY",
                "env",
                required=True,
            ),
        ]

    def get_stats(self, user_id: str) -> dict[str, Any]:
        return {
            "sidecar": self._sidecar.stats(user_id),
            "metrics": METRICS.snapshot(self.name, user_id),
            "telemetry_disabled": os.getenv("MEM0_TELEMETRY") == "false",
            "batch_write_native": self._batch_write_native,
        }

    def get_capability_profile(self) -> CapabilityProfile:
        batch_level = "native" if self._batch_write_native else "declared"
        batch_note = (
            "mem0.add 支持 list messages 单次写入"
            if self._batch_write_native
            else "默认实现循环调 write"
        )

        caps_map = {
            "physical_clear": ("native", "mem0.delete_all 同步物理删"),
            "turn_id_traceback": ("wrapped", "metadata.turn_id + sidecar 兜底"),
            "cognitive_type_filter": ("declared", "mem0 不原生 cognitive 分类"),
            "score_normalized": ("native", "mem0 返 [0,1]"),
            "concurrent_safe": ("native", "user_id 物理隔离"),
            "cost_accounting": ("computed", "litellm usage 字段抽取"),
            "embedding_export": ("computed", "EmbeddingService 重 embed（统一空间）"),
            "flush_blocking": ("native", "mem0 write 同步"),
            "consolidate_explicit": ("native", "mem0 在 write 时同步做 consolidation"),
            "batch_write_native": (batch_level, batch_note),
            "write_semantic_sync": ("native", "返回时事实已可查"),
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
                "C": "incompatible",
            },
            lane_notes={
                "A3": "本地 7B 在实体抽取格式上可能不稳定",
                "C": "mem0 依赖 internal_llm，无法纯检索运行",
            },
        )

    @staticmethod
    def _as_dict(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump") and callable(item.model_dump):
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return dumped
        if hasattr(item, "dict") and callable(item.dict):
            dumped = item.dict()
            if isinstance(dumped, dict):
                return dumped
        if hasattr(item, "__dict__") and isinstance(item.__dict__, dict):
            return dict(item.__dict__)
        return {}

    @classmethod
    def _normalize_records(cls, result: Any) -> list[Any]:
        if isinstance(result, list):
            return result
        if isinstance(result, tuple):
            return list(result)

        data = cls._as_dict(result)
        for key in ("results", "memories", "data", "items"):
            values = data.get(key)
            if isinstance(values, list):
                return values

        if data:
            return [data]
        return []

    @classmethod
    def _extract_memory_ids(cls, result: Any) -> list[str]:
        memory_ids: list[str] = []
        for item in cls._normalize_records(result):
            memory_id = cls._extract_memory_id(item)
            if memory_id:
                memory_ids.append(memory_id)
        return sorted(set(memory_ids))

    @classmethod
    def _extract_memory_id(cls, item: Any) -> str:
        data = cls._as_dict(item)
        for key in ("id", "memory_id", "uuid"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        if isinstance(item, str):
            return item
        return ""

    @classmethod
    def _extract_memory_text(cls, item: Any) -> str:
        data = cls._as_dict(item)
        for key in ("memory", "text", "content", "value"):
            value = data.get(key)
            if isinstance(value, str):
                return value
        return ""

    @classmethod
    def _extract_memory_score(cls, item: Any) -> float:
        data = cls._as_dict(item)
        score = data.get("score", 0.0)
        if isinstance(score, (int, float)):
            return float(score)
        return 0.0

    @classmethod
    def _extract_timestamp(cls, item: Any) -> float | None:
        data = cls._as_dict(item)
        for key in ("updated_at", "created_at", "timestamp", "ts"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    @classmethod
    def _extract_metadata(cls, item: Any) -> dict[str, Any]:
        data = cls._as_dict(item)
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        return {}

    def _extract_source_turn_ids(self, *, user_id: str, item: Any, memory_id: str) -> list[str]:
        metadata = self._extract_metadata(item)

        turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
        if isinstance(turn_id, str) and turn_id:
            return [turn_id]

        turn_ids = metadata.get("turn_ids") if isinstance(metadata, dict) else None
        if isinstance(turn_ids, list):
            values = [str(v) for v in turn_ids if isinstance(v, str) and v]
            if values:
                return values

        if memory_id:
            return self._sidecar.get_turn_ids(user_id, memory_id)
        return []
