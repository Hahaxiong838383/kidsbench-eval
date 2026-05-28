"""MemoryOSAdapter：将 MemoryOS 三层记忆系统翻译到 KidsBench 契约。"""
from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable
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
    EmbeddingService,
    GlobalRateLimiter,
    LogicError,
    NetworkError,
    RateLimitError,
    SidecarStore,
    StructuredLogger,
    TimeoutError_,
    get_clock,
    track_metrics,
    wrap_errors,
)

_OPENAI_PROVIDER = "openai"
# gemini Wave 1 review Finding MemoryOS.2 修复后的默认值
# rate: 每秒 1000 tokens（约 dashscope/OpenAI 自由计划的 1/10）
# burst: 5000（允许评测初始化一次性灌 ~10 个长 turn）
_DEFAULT_RATE = 1000.0
_DEFAULT_BURST = 5000
_RETRYABLE_STATUS_PATTERN = re.compile(r"\b(429|500|502|503|504)\b")


class _HashEmbeddingService(EmbeddingService):
    """本地兜底 embedding，避免契约测试依赖外部模型服务。"""

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            seed = text or " "
            vec = [0.0] * self._dim
            for idx, ch in enumerate(seed):
                bucket = idx % self._dim
                vec[bucket] += (ord(ch) % 97) / 97.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors

    def dim(self) -> int:
        return self._dim


class MemoryOSAdapter(MemoryAdapter):
    """MemoryOS 适配器（per-user manager + 显式 consolidate）。"""

    name = "memoryos"
    paradigm_tags = {
        "representation": "raw+vector+三层",
        "retrieval": "tiered_consolidation",
        "write_policy": "consolidation",
        "controller": "os_inspired",
        "cognitive": ["episodic", "semantic"],
    }

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        sidecar: SidecarStore | None = None,
        embedding_service: EmbeddingService | None = None,
        rate_limiter: GlobalRateLimiter | None = None,
        logger: StructuredLogger | None = None,
        stm_capacity: int = 5,
        auto_consolidate: bool = False,
    ) -> None:
        self._config = config or {}
        self._sidecar = sidecar or SidecarStore(backend="memory")
        self._embed = embedding_service or _HashEmbeddingService()
        self._rate_limiter = rate_limiter or GlobalRateLimiter()
        self._logger = logger or StructuredLogger(adapter_name=self.name)
        self._stm_capacity = stm_capacity
        self._auto_consolidate = auto_consolidate

        self._manager_lock = threading.Lock()
        self._manager_by_user: dict[str, Any] = {}
        self._last_consolidate_at: dict[str, float] = {}

        self._mm_factory: Callable[..., Any] = self._resolve_manager_factory(self._config)
        self._ensure_rate_limiter_provider()

    @track_metrics(method="write")
    @wrap_errors(
        mapping={
            "openai.RateLimitError": RateLimitError,
            "openai.APITimeoutError": TimeoutError_,
            "openai.APIConnectionError": NetworkError,
            "openai.AuthenticationError": LogicError,
            "httpx.TimeoutException": TimeoutError_,
            "httpx.HTTPError": NetworkError,
        }
    )
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        self._require_user(user_id)
        # gemini Wave 1 review Finding MemoryOS.2: 不能硬编码 tokens=1，
        # 大文本写入时会瞬间打爆 OpenAI 限流。按 text 长度估算（≈ 4 字符/token）。
        self._acquire_rate_limit(tokens=self._estimate_tokens(turn.text))

        mm = self._get_manager(user_id)
        write_result = mm.write(
            user_input=turn.text if turn.role == "user" else "",
            system_response=turn.text if turn.role == "assistant" else "",
            metadata={
                "turn_id": turn.turn_id,
                "ts": turn.timestamp,
                "user_id": user_id,
                **turn.metadata,
            },
        )

        memory_ids = self._extract_memory_ids_from_write(write_result)
        if not memory_ids:
            memory_ids = [f"stm::{user_id}::{turn.turn_id}::{int(turn.timestamp * 1000)}"]
        self._sidecar.put(user_id, turn.turn_id, memory_ids)

        if self._auto_consolidate:
            self.consolidate(user_id)

        return WriteStats(
            success=True,
            cost_token=self._estimate_token_cost(write_result),
            raw={"memory_ids": memory_ids},
        )

    def batch_write(self, user_id: str, turns: list[Turn]) -> list[WriteStats]:
        """MemoryOS 无原生 batch API，保留默认循环 write。"""
        return super().batch_write(user_id=user_id, turns=turns)

    @track_metrics(method="read")
    @wrap_errors(
        mapping={
            "openai.RateLimitError": RateLimitError,
            "openai.APITimeoutError": TimeoutError_,
            "openai.APIConnectionError": NetworkError,
            "httpx.TimeoutException": TimeoutError_,
            "httpx.HTTPError": NetworkError,
        }
    )
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        self._require_user(user_id)
        read_opts = opts or ReadOpts()
        mm = self._get_manager(user_id)
        top_k = max(int(read_opts.top_k), 1)
        raw_result = mm.retrieve(query=query, context_window=top_k)
        rows = self._normalize_rows(raw_result)
        rows = rows[:top_k]

        texts = [str(self._pick_value(row, "text", "content", "memory", default="")) for row in rows]
        embeddings = self._embed.embed(texts) if texts else []
        memories: list[Memory] = []
        for idx, row in enumerate(rows):
            text = texts[idx]
            score = self._normalize_score(self._pick_value(row, "score", default=1.0))
            metadata = self._normalize_metadata(self._pick_value(row, "metadata", default={}))
            memory_id = str(
                self._pick_value(row, "id", "memory_id", "uid", default=f"mem_{user_id}_{idx}")
            )
            source_turn_ids = self._collect_source_turn_ids(user_id, memory_id, metadata)
            if read_opts.cognitive_filter:
                ctype = str(metadata.get("cognitive_type", ""))
                if ctype and ctype not in read_opts.cognitive_filter:
                    continue

            memories.append(
                Memory(
                    memory_id=memory_id,
                    text=text,
                    score=score,
                    source_turn_ids=source_turn_ids,
                    source_embedding=embeddings[idx] if idx < len(embeddings) else None,
                    timestamp=self._to_float(self._pick_value(row, "timestamp", "ts", default=None)),
                    metadata=metadata,
                )
            )

        if read_opts.score_threshold > 0:
            memories = [m for m in memories if m.score >= read_opts.score_threshold]

        return ReadResult(
            memories=memories,
            cost_token=self._estimate_token_cost(raw_result),
            raw={"rows": len(rows)},
        )

    @track_metrics(method="clear")
    @wrap_errors(
        mapping={
            "openai.RateLimitError": RateLimitError,
            "openai.APITimeoutError": TimeoutError_,
            "openai.APIConnectionError": NetworkError,
            "httpx.TimeoutException": TimeoutError_,
            "httpx.HTTPError": NetworkError,
        }
    )
    def clear(self, user_id: str) -> ClearStats:
        self._require_user(user_id)
        mm = self._get_manager(user_id)
        mm.reset_all()
        deleted = self._sidecar.clear_user(user_id)
        with self._manager_lock:
            self._manager_by_user.pop(user_id, None)
        return ClearStats(success=True, deleted_count=deleted)

    @track_metrics(method="flush")
    def flush(self, user_id: str) -> FlushStats:
        self._require_user(user_id)
        # MemoryOS STM 写入同步完成；flush 仅作为协议对齐点。
        return FlushStats(success=True)

    @track_metrics(method="consolidate")
    @wrap_errors(
        mapping={
            "openai.RateLimitError": RateLimitError,
            "openai.APITimeoutError": TimeoutError_,
            "openai.APIConnectionError": NetworkError,
            "openai.InternalServerError": NetworkError,
            "httpx.TimeoutException": TimeoutError_,
            "httpx.HTTPError": NetworkError,
        }
    )
    def consolidate(self, user_id: str) -> ConsolidateStats:
        self._require_user(user_id)
        mm = self._get_manager(user_id)
        retries = 3
        # consolidate 内部会调多次 LLM（短/中/长层各一次），真正的 token 限流在 LLMClient 层做
        # 这里 acquire 只是触发计数，给 GlobalRateLimiter 一个"调用次数"信号
        # 真实 token 限流由内部 LLM 调用各自 acquire（按实际 prompt/response 长度）
        consolidate_tokens = self._config.get("consolidate_acquire_tokens", 8)
        for attempt in range(retries):
            self._acquire_rate_limit(tokens=consolidate_tokens)
            try:
                result = mm.consolidate()
                self._last_consolidate_at[user_id] = get_clock().now()
                return ConsolidateStats(
                    success=True,
                    cost_token=self._estimate_token_cost(result),
                    consolidated_count=self._extract_consolidated_count(result),
                    error=None,
                )
            except Exception as err:
                retryable = self._is_retryable_consolidate_error(err)
                last_try = attempt + 1 >= retries
                if not retryable or last_try:
                    raise
                sleep_s = 0.5 * (2**attempt)
                self._logger.warn(
                    "consolidate_retry",
                    user_id=user_id,
                    attempt=attempt + 1,
                    sleep_s=sleep_s,
                    error=str(err),
                )
                time.sleep(sleep_s)
        raise AdapterError("consolidate exhausted retries")

    def get_dependencies(self) -> list[Dependency]:
        return [
            Dependency("memoryos-pypi", "model", required=True, swap_supported=False),
            Dependency(
                "openai gpt-4 or gpt-3.5",
                "internal_llm",
                required=True,
                swap_supported=False,
            ),
            Dependency("ada-002", "internal_embed", required=True, swap_supported=False),
            Dependency("OPENAI_API_KEY", "env", required=True),
        ]

    def get_stats(self, user_id: str) -> dict[str, Any]:
        self._require_user(user_id)
        sidecar_stats = self._sidecar.stats(user_id)
        mm = self._manager_by_user.get(user_id)
        layer_sizes = {"stm_size": 0, "mtm_size": 0, "ltm_size": 0}
        if mm is not None:
            layer_sizes["stm_size"] = self._layer_size(mm, "stm", "short", "short_term_memory")
            layer_sizes["mtm_size"] = self._layer_size(mm, "mtm", "mid", "mid_term_memory")
            layer_sizes["ltm_size"] = self._layer_size(mm, "ltm", "long", "long_term_memory")

        return {
            **sidecar_stats,
            **layer_sizes,
            "last_consolidate_at": self._last_consolidate_at.get(user_id),
        }

    def get_capability_profile(self) -> CapabilityProfile:
        caps_map: dict[str, tuple[str, str]] = {
            "physical_clear": ("native", "MemoryOS reset_all 同步删内存"),
            "turn_id_traceback": ("wrapped", "STM 原生保留 + LTM 靠 sidecar 兜底"),
            "cognitive_type_filter": (
                "declared",
                "三层架构本身近似 episodic→semantic 演化，但不支持显式 cognitive filter",
            ),
            "score_normalized": ("declared", "MemoryOS 返的 score 未明确归一化，统一映射 [0,1]"),
            "concurrent_safe": ("wrapped", "原生单实例，adapter 按 user_id 隔离实例"),
            "cost_accounting": ("simulated", "LLM 调用 token 估算（基于 prompt + response 字数）"),
            "embedding_export": ("computed", "用统一 embedding service 重 embed（辅路）"),
            "flush_blocking": ("native", "STM 同步写"),
            "consolidate_explicit": ("native", "显式 consolidate API，跟 flush 严格分离"),
            "batch_write_native": ("declared", "无原生 batch，默认循环 write"),
            "write_semantic_sync": ("wrapped", "STM 同步可查；LTM 需 consolidate 后才有"),
            "lineage_after_consolidate": ("declared", "consolidate 抽象归纳 LTM 时丢失 turn_id（gemini A.1 known issue）"),
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
                "A1": "degraded",
                "A2": "compatible",
                "A3": "incompatible",
                "B": "compatible",
                "C": "incompatible",
            },
            lane_notes={
                "A1": "OpenAI prompt 模板强锁，Qwen3-Max 抽取 entity 时 JSON 偶崩，需 Verifier 兜底",
                "A3": "本地 7B 完全无法跑三层固化 pipeline",
                "C": "MemoryOS 设计上必须依赖 LLM 做 consolidation",
            },
        )

    def _get_manager(self, user_id: str) -> Any:
        with self._manager_lock:
            manager = self._manager_by_user.get(user_id)
            if manager is None:
                manager = self._mm_factory(
                    user_id=user_id,
                    stm_capacity=self._stm_capacity,
                    config=self._config,
                )
                self._manager_by_user[user_id] = manager
            return manager

    def _resolve_manager_factory(self, config: dict[str, Any]) -> Callable[..., Any]:
        custom = config.get("memory_manager_factory")
        if callable(custom):
            return custom
        return self._default_memory_manager_factory

    def _default_memory_manager_factory(self, **kwargs: Any) -> Any:
        import importlib

        candidates = [
            ("memoryos", "MemoryManager"),
            ("memoryos.memory_manager", "MemoryManager"),
            ("memoryos_pypi", "MemoryManager"),
        ]
        for module_name, attr_name in candidates:
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            cls = getattr(module, attr_name, None)
            if cls is None:
                continue
            try:
                return cls(stm_capacity=kwargs.get("stm_capacity", self._stm_capacity))
            except TypeError:
                return cls()
        raise AdapterError(
            "memoryos-pypi is required: install via `.venv/bin/pip install memoryos-pypi` "
            "or pass config['memory_manager_factory'] for tests"
        )

    def _ensure_rate_limiter_provider(self) -> None:
        try:
            self._rate_limiter.try_acquire(_OPENAI_PROVIDER, tokens=0)
        except AdapterError:
            self._rate_limiter.register(
                provider=_OPENAI_PROVIDER,
                rate=float(self._config.get("openai_rate_per_sec", _DEFAULT_RATE)),
                burst=int(self._config.get("openai_burst", _DEFAULT_BURST)),
            )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """估算 LLM token 消耗：英文 ≈ 4 字符/token，中文 ≈ 1.5 字符/token。

        gemini review Finding MemoryOS.2 修复：避免硬编码 tokens=1。
        """
        if not text:
            return 1
        # 中文比例高的话 token/char 更接近 1，这里用保守估算（偏高）
        return max(1, len(text) // 3)

    def _acquire_rate_limit(self, *, tokens: int) -> None:
        if not self._rate_limiter.try_acquire(_OPENAI_PROVIDER, tokens=tokens):
            raise RateLimitError("openai provider rate limited by adapter token bucket")

    @staticmethod
    def _require_user(user_id: str) -> None:
        if not user_id:
            raise AdapterError("user_id must not be empty")

    @staticmethod
    def _normalize_rows(raw_result: Any) -> list[Any]:
        if raw_result is None:
            return []
        if isinstance(raw_result, list):
            return raw_result
        if isinstance(raw_result, dict):
            for key in ("memories", "results", "items", "data"):
                rows = raw_result.get(key)
                if isinstance(rows, list):
                    return rows
            return [raw_result]
        rows = getattr(raw_result, "memories", None) or getattr(raw_result, "results", None)
        if isinstance(rows, list):
            return rows
        return [raw_result]

    @staticmethod
    def _pick_value(obj: Any, *keys: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            for key in keys:
                if key in obj:
                    return obj[key]
            return default
        for key in keys:
            if hasattr(obj, key):
                return getattr(obj, key)
        return default

    def _collect_source_turn_ids(
        self,
        user_id: str,
        memory_id: str,
        metadata: dict[str, Any],
    ) -> list[str]:
        turn_ids = set(self._sidecar.get_turn_ids(user_id, memory_id))
        meta_tid = metadata.get("turn_id")
        if isinstance(meta_tid, str) and meta_tid:
            turn_ids.add(meta_tid)
        if isinstance(meta_tid, list):
            turn_ids.update(t for t in meta_tid if isinstance(t, str) and t)
        return sorted(turn_ids)

    @staticmethod
    def _normalize_metadata(meta: Any) -> dict[str, Any]:
        if isinstance(meta, dict):
            return meta
        if meta is None:
            return {}
        return {"raw_metadata": meta}

    @staticmethod
    def _normalize_score(value: Any) -> float:
        score = 1.0
        if isinstance(value, (int, float)):
            score = float(value)
        return max(0.0, min(1.0, score))

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _extract_memory_ids_from_write(self, result: Any) -> list[str]:
        if result is None:
            return []
        if isinstance(result, dict):
            return self._extract_ids_from_dict(result)
        if isinstance(result, list):
            return [str(item) for item in result if item]
        ids: list[str] = []
        for key in ("id", "memory_id", "stm_id"):
            value = getattr(result, key, None)
            if value:
                ids.append(str(value))
        created = getattr(result, "created_memories", None)
        if isinstance(created, list):
            for item in created:
                ids.extend(self._extract_memory_ids_from_write(item))
        if ids:
            return sorted(set(ids))
        return []

    def _extract_ids_from_dict(self, payload: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        for key in ("id", "memory_id", "stm_id"):
            value = payload.get(key)
            if value:
                ids.append(str(value))
        for key in ("memory_ids", "ids"):
            value = payload.get(key)
            if isinstance(value, list):
                ids.extend(str(v) for v in value if v)
        created = payload.get("created_memories")
        if isinstance(created, list):
            for item in created:
                if isinstance(item, dict):
                    ids.extend(self._extract_ids_from_dict(item))
                elif item:
                    ids.append(str(item))
        return sorted(set(ids))

    @staticmethod
    def _estimate_token_cost(payload: Any) -> int:
        if payload is None:
            return 0
        if isinstance(payload, dict):
            usage = payload.get("usage")
            if isinstance(usage, dict):
                prompt = int(usage.get("prompt_tokens", 0) or 0)
                completion = int(usage.get("completion_tokens", 0) or 0)
                total = int(usage.get("total_tokens", 0) or 0)
                return total or (prompt + completion)
            text = str(payload)
            return max(1, len(text) // 4)
        if hasattr(payload, "usage"):
            usage = getattr(payload, "usage", None)
            if isinstance(usage, dict):
                return MemoryOSAdapter._estimate_token_cost({"usage": usage})
        if hasattr(payload, "__dict__"):
            return max(1, len(str(payload.__dict__)) // 4)
        return max(1, len(str(payload)) // 4)

    @staticmethod
    def _extract_consolidated_count(payload: Any) -> int:
        if isinstance(payload, dict):
            for key in ("consolidated_count", "merged_count", "count"):
                value = payload.get(key)
                if isinstance(value, int):
                    return value
            rows = payload.get("memories") or payload.get("results")
            if isinstance(rows, list):
                return len(rows)
            return 0
        value = getattr(payload, "consolidated_count", None)
        if isinstance(value, int):
            return value
        rows = getattr(payload, "memories", None) or getattr(payload, "results", None)
        if isinstance(rows, list):
            return len(rows)
        return 0

    @staticmethod
    def _is_retryable_consolidate_error(err: Exception) -> bool:
        if isinstance(err, (RateLimitError, TimeoutError_)):
            return True
        if isinstance(err, NetworkError):
            return True
        if isinstance(err, AdapterError):
            return bool(_RETRYABLE_STATUS_PATTERN.search(str(err)))
        return bool(_RETRYABLE_STATUS_PATTERN.search(str(err)))

    @staticmethod
    def _layer_size(manager: Any, *names: str) -> int:
        for name in names:
            layer = getattr(manager, name, None)
            if layer is None:
                continue
            guessed = MemoryOSAdapter._guess_size(layer)
            if guessed is not None:
                return guessed
        return 0

    @staticmethod
    def _guess_size(obj: Any) -> int | None:
        for attr in ("memories", "items", "records", "store", "data", "buffer", "index"):
            value = getattr(obj, attr, None)
            if value is not None:
                try:
                    return len(value)
                except TypeError:
                    continue
        try:
            return len(obj)
        except TypeError:
            return None
