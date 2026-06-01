"""GraphitiAdapter: temporal KG adapter for getzep/graphiti."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from collections.abc import Iterable
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
    EmbeddingService,
    GlobalRateLimiter,
    SidecarStore,
    StructuredLogger,
    check_cpu_avx2,
    get_clock,
)


class GraphitiAdapter(MemoryAdapter):
    """Adapter that bridges Graphiti async APIs to KidsBench sync contract."""

    name = "graphiti"
    paradigm_tags = {
        "representation": "temporal_kg",
        "retrieval": "16_recipes_multi_hop",
        "write_policy": "event_chain",
        "controller": "rule",
        "cognitive": ["episodic", "semantic", "procedural"],
    }

    def __init__(
        self,
        *,
        backend: str = "falkordb",
        uri: str = "redis://localhost:6379",
        config: dict[str, Any] | None = None,
        sidecar: SidecarStore | None = None,
        embedding_service: EmbeddingService | None = None,
        rate_limiter: GlobalRateLimiter | None = None,
        logger: StructuredLogger | None = None,
        search_config: dict[str, Any] | str | None = None,
    ) -> None:
        if backend not in {"neo4j", "falkordb"}:
            raise AdapterError(f"unsupported graphiti backend: {backend}")

        self._backend = backend
        self._uri = uri
        self._config = dict(config or {})
        # A 决策：harness 在 config 传入注入的 model（model 实际在 client_factory 内用，adapter 自报供校验）
        self._injected_llm = str(self._config.get("injected_llm_model", ""))
        self._injected_embed = str(self._config.get("injected_embed_model", ""))
        self._sidecar = sidecar or SidecarStore()
        self._embedding_service = embedding_service
        self._rate_limiter = rate_limiter
        self._logger = logger
        self._default_search_config = (
            search_config
            or self._config.get("default_search_config")
            or self._config.get("search_config")
            or "COMBINED_HYBRID_SEARCH_RRF"
        )
        self._sessions_by_user: dict[str, set[str]] = {}
        self._turn_ids_by_user: dict[str, set[str]] = {}
        self._batch_write_native = False

        if self._backend == "falkordb" and not self._config.get("skip_avx2_check", False):
            if not check_cpu_avx2():
                raise AdapterError("FalkorDB requires AVX2")

        self._client = self._build_client()

    def write(self, user_id: str, turn: Turn) -> WriteStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")

        t0 = time.time()
        session_name = self._session_name(user_id, turn.session_id)
        metadata = {
            **(turn.metadata if isinstance(turn.metadata, dict) else {}),
            "turn_id": turn.turn_id,
            "ts": turn.timestamp,
            "role": turn.role,
            "current_time": _iso_time(get_clock().now()),
            # user_id 显式传入 metadata，避免 wrapper 从 session_name 解析时
            # user_id 含 underscore 被截断（如 'eval_graphiti_q_001' 会被 split 错位）
            "user_id": user_id,
        }
        try:
            self._acquire_provider_token("openai", tokens=1)
            payload = self._run(
                self._client.add_episode(
                    name=session_name,
                    episode_body=turn.text,
                    metadata=metadata,
                )
            )
            memory_ids = _extract_memory_ids(payload)
            self._sidecar.put(user_id, turn.turn_id, memory_ids)
            self._sessions_by_user.setdefault(user_id, set()).add(session_name)
            self._turn_ids_by_user.setdefault(user_id, set()).add(turn.turn_id)
            self._log("write_ok", user_id=user_id, turn_id=turn.turn_id, memory_count=len(memory_ids))
            return WriteStats(
                success=True,
                latency_ms=(time.time() - t0) * 1000,
                raw={"session_name": session_name, "memory_ids": memory_ids},
            )
        except AdapterError:
            raise
        except Exception as err:
            self._log("write_fail", user_id=user_id, turn_id=turn.turn_id, error=str(err))
            raise AdapterError(f"graphiti write failed: {type(err).__name__}: {err}") from err

    def batch_write(self, user_id: str, turns: list[Turn]) -> list[WriteStats]:
        if not turns:
            return []
        if not user_id:
            raise AdapterError("user_id must not be empty")

        bulk_method = self._detect_bulk_method()
        if bulk_method is None:
            self._batch_write_native = False
            return super().batch_write(user_id, turns)

        t0 = time.time()
        items: list[dict[str, Any]] = []
        for turn in turns:
            items.append(
                {
                    "name": self._session_name(user_id, turn.session_id),
                    "episode_body": turn.text,
                    "metadata": {
                        **(turn.metadata if isinstance(turn.metadata, dict) else {}),
                        "turn_id": turn.turn_id,
                        "ts": turn.timestamp,
                        "role": turn.role,
                        "current_time": _iso_time(get_clock().now()),
                    },
                }
            )

        try:
            self._acquire_provider_token("openai", tokens=len(turns))
            payload = self._run(bulk_method(items))
            chunked = _split_bulk_payload(payload, len(turns))
            latency = (time.time() - t0) * 1000
            stats: list[WriteStats] = []
            for turn, row in zip(turns, chunked, strict=True):
                memory_ids = _extract_memory_ids(row)
                self._sidecar.put(user_id, turn.turn_id, memory_ids)
                self._sessions_by_user.setdefault(user_id, set()).add(
                    self._session_name(user_id, turn.session_id)
                )
                self._turn_ids_by_user.setdefault(user_id, set()).add(turn.turn_id)
                stats.append(
                    WriteStats(
                        success=True,
                        latency_ms=latency / max(len(turns), 1),
                        raw={"memory_ids": memory_ids},
                    )
                )
            self._batch_write_native = True
            return stats
        except Exception:
            self._batch_write_native = False
            return super().batch_write(user_id, turns)

    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        if not user_id:
            raise AdapterError("user_id must not be empty")

        t0 = time.time()
        read_opts = opts or ReadOpts()
        search_cfg = read_opts.extra.get("search_config", self._default_search_config)
        try:
            payload = self._run(self._client.search(query=query, search_config=search_cfg))
            records = _extract_search_records(payload)
            memories: list[Memory] = []
            for rec in records:
                memory_id = str(rec.get("memory_id") or "")
                if not memory_id:
                    continue

                score = _clamp_score(float(rec.get("score", 0.0)))
                if score < read_opts.score_threshold:
                    continue

                source_ids = set(self._sidecar.get_turn_ids(user_id, memory_id))
                if read_opts.include_provenance:
                    source_ids.update(self._resolve_turn_ids_via_graph(user_id, rec))
                if not self._record_belongs_to_user(user_id, rec, source_ids):
                    continue

                text = str(rec.get("text") or f"[{rec.get('kind', 'node')}] {memory_id}")
                source_embedding: list[float] | None = None
                if self._embedding_service is not None:
                    source_embedding = self._embedding_service.embed([text])[0]

                memories.append(
                    Memory(
                        memory_id=memory_id,
                        text=text,
                        score=score,
                        source_turn_ids=sorted(source_ids),
                        source_embedding=source_embedding,
                        timestamp=rec.get("timestamp"),
                        metadata=rec.get("metadata", {}),
                    )
                )

            if read_opts.cognitive_filter:
                allowed = set(read_opts.cognitive_filter)
                memories = [
                    m
                    for m in memories
                    if str(m.metadata.get("cognitive_type", "")).strip() in allowed
                ]

            memories = memories[: max(read_opts.top_k, 0)]
            return ReadResult(memories=memories, latency_ms=(time.time() - t0) * 1000, raw={"search_config": search_cfg})
        except AdapterError:
            raise
        except Exception as err:
            raise AdapterError(f"graphiti read failed: {type(err).__name__}: {err}") from err

    def clear(self, user_id: str) -> ClearStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")

        t0 = time.time()
        sessions = sorted(self._sessions_by_user.get(user_id, set()))
        deleted = 0
        try:
            for session_name in sessions:
                self._run(self._client.delete_session(name=session_name))
                deleted += 1

            time.sleep(0.5)
            if not self._verify_user_empty(user_id):
                raise AdapterError("graphiti clear verification failed: search still returns results")

            self._sidecar.clear_user(user_id)
            self._sessions_by_user.pop(user_id, None)
            self._turn_ids_by_user.pop(user_id, None)
            return ClearStats(
                success=True,
                latency_ms=(time.time() - t0) * 1000,
                deleted_count=deleted,
            )
        except AdapterError:
            raise
        except Exception as err:
            raise AdapterError(f"graphiti clear failed: {type(err).__name__}: {err}") from err

    def flush(self, user_id: str) -> FlushStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.time()
        try:
            flush_pending = getattr(self._client, "flush_pending", None)
            if callable(flush_pending):
                self._run(flush_pending())
            return FlushStats(success=True, latency_ms=(time.time() - t0) * 1000)
        except Exception as err:
            raise AdapterError(f"graphiti flush failed: {type(err).__name__}: {err}") from err

    def consolidate(self, user_id: str) -> ConsolidateStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.time()
        consolidated = 0
        try:
            consolidate_method = getattr(self._client, "consolidate_session", None)
            if callable(consolidate_method):
                for session_name in sorted(self._sessions_by_user.get(user_id, set())):
                    self._acquire_provider_token("openai", tokens=1)
                    self._run(consolidate_method(name=session_name))
                    consolidated += 1
            return ConsolidateStats(
                success=True,
                latency_ms=(time.time() - t0) * 1000,
                consolidated_count=consolidated,
            )
        except Exception as err:
            raise AdapterError(f"graphiti consolidate failed: {type(err).__name__}: {err}") from err

    def get_dependencies(self) -> list[Dependency]:
        deps: list[Dependency] = [
            Dependency("graphiti-core", "model", required=True, swap_supported=False),
            Dependency(
                "falkordb" if self._backend == "falkordb" else "neo4j",
                "service",
                required=True,
                check_hint="redis-cli -p 6379 ping" if self._backend == "falkordb" else "neo4j status",
                swap_supported=True,
            ),
            Dependency(
                "cpu_avx2",
                "model",
                required=self._backend == "falkordb",
                check_hint="check_cpu_avx2()",
                swap_supported=False,
            ),
            Dependency("openai gpt-4o", "internal_llm", required=True, swap_supported=True),
            Dependency("openai text-embedding-3-small", "internal_embed", required=True, swap_supported=True),
            Dependency("OPENAI_API_KEY", "env", required=True, check_hint="env OPENAI_API_KEY"),
        ]
        return deps

    def get_injected_providers(self) -> dict[str, str]:
        """A 决策：自报实际注入的 LLM/embedding（harness 经 config 传入），供锁定校验。"""
        return {"internal_llm": self._injected_llm, "internal_embed": self._injected_embed}

    def get_stats(self, user_id: str) -> dict[str, Any]:
        stats = {
            **self._sidecar.stats(user_id),
            "session_count": len(self._sessions_by_user.get(user_id, set())),
        }
        try:
            graph_stats = self._collect_graph_counts(user_id)
            stats.update(graph_stats)
        except Exception:
            stats.update({"node_count": -1, "edge_count": -1, "episode_count": -1})
        return stats

    def get_capability_profile(self) -> CapabilityProfile:
        batch_level = (
            ("native", "graphiti add_episode_bulk/add_episodes 批量写入")
            if self._batch_write_native
            else ("declared", "bulk API 未检测到，回退默认 batch_write 循环")
        )
        caps_map: dict[str, tuple[str, str]] = {
            "physical_clear": ("wrapped", "delete_session + sleep + verify search empty"),
            "turn_id_traceback": ("wrapped", "metadata + sidecar + 图回溯三重保险"),
            "cognitive_type_filter": ("computed", "episodic/semantic/procedural 可从 KG node label 区分"),
            "score_normalized": ("native", "graphiti RRF 已归一化"),
            "concurrent_safe": ("native", "session name 前缀包含 user_id"),
            "cost_accounting": ("native", "graphiti 内部可统计 LLM usage"),
            "embedding_export": ("computed", "中间层重 embed（统一空间）"),
            "flush_blocking": ("native", "flush_pending 或同步写入完成语义"),
            "consolidate_explicit": ("declared", "write 阶段 Saga 已做消歧合并，无独立强制流程"),
            "batch_write_native": batch_level,
            "write_semantic_sync": ("native", "Saga 写入返回后可检索"),
            "lineage_after_consolidate": ("wrapped", "Episode→Entity 合并时部分丢失，靠 sidecar + 图回溯兜底"),
        }
        caps = [
            Capability(feature=f, level=lvl, note=note)  # type: ignore[arg-type]
            for f in STANDARD_FEATURES
            for lvl, note in [caps_map[f]]
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
                "A1": "Qwen + Instructor 场景偶发结构化输出崩溃，建议降级兜底",
                "C": "Graphiti 强依赖 internal LLM 提取，无法跑纯检索档",
            },
        )

    def _build_client(self) -> Any:
        explicit_client = self._config.get("client")
        if explicit_client is not None:
            return explicit_client

        factory = self._config.get("client_factory")
        if callable(factory):
            return factory(backend=self._backend, uri=self._uri, config=self._config)

        candidates = [
            ("graphiti_core", "GraphitiClient"),
            ("graphiti_core", "Graphiti"),
            ("graphiti_core.client", "GraphitiClient"),
            ("graphiti", "GraphitiClient"),
        ]
        for module_name, class_name in candidates:
            try:
                module = importlib.import_module(module_name)
                cls = getattr(module, class_name, None)
                if cls is None:
                    continue
                kwargs = {k: v for k, v in self._config.items() if k not in {"client", "client_factory"}}
                kwargs.setdefault("backend", self._backend)
                kwargs.setdefault("uri", self._uri)
                return cls(**kwargs)
            except Exception:
                continue

        raise AdapterError(
            "graphiti client unavailable; install graphiti-core or pass config['client'] / config['client_factory']"
        )

    def _detect_bulk_method(self):
        add_episode_bulk = getattr(self._client, "add_episode_bulk", None)
        if callable(add_episode_bulk):
            return add_episode_bulk
        add_episodes = getattr(self._client, "add_episodes", None)
        if callable(add_episodes):
            return add_episodes
        return None

    def _resolve_turn_ids_via_graph(self, user_id: str, record: dict[str, Any]) -> set[str]:
        turn_ids = set(_extract_turn_ids(record))
        memory_id = str(record.get("memory_id") or "")
        if not memory_id:
            return turn_ids

        provenance_method = getattr(self._client, "query_provenance", None)
        if callable(provenance_method):
            payload = self._run(provenance_method(memory_id=memory_id, user_id=user_id))
            turn_ids.update(_extract_turn_ids(payload))
            return turn_ids

        query_method = getattr(self._client, "query", None)
        if callable(query_method):
            payload = self._run(
                query_method(
                    cypher=(
                        "MATCH (n)-[*1..3]->(e) "
                        "WHERE n.id = $memory_id OR ID(n) = toInteger($memory_id) "
                        "RETURN e.turn_id AS turn_id, e.metadata AS metadata LIMIT 20"
                    ),
                    params={"memory_id": memory_id},
                )
            )
            turn_ids.update(_extract_turn_ids(payload))
        return turn_ids

    def _collect_graph_counts(self, user_id: str) -> dict[str, int]:
        getter = getattr(self._client, "get_stats", None)
        if callable(getter):
            payload = self._run(getter(user_id=user_id))
            return {
                "node_count": int(_extract_named_count(payload, "node_count")),
                "edge_count": int(_extract_named_count(payload, "edge_count")),
                "episode_count": int(_extract_named_count(payload, "episode_count")),
            }

        return {
            "node_count": -1,
            "edge_count": -1,
            "episode_count": len(self._sessions_by_user.get(user_id, set())),
        }

    def _verify_user_empty(self, user_id: str) -> bool:
        probe = f"u_{user_id}"
        # 用 dict config 限定 group_ids=[user_id]，避免召回其他 user 的数据让 verify 永远失败
        # 真实 wrapper 需要 dict 才能拿到 group_ids；Mock client 也能接受 dict
        cfg = {"group_ids": [user_id], "num_results": 5}
        payload = self._run(self._client.search(query=probe, search_config=cfg))
        records = _extract_search_records(payload)
        return not records

    def _run(self, result: Any) -> Any:
        """同步桥接 async/sync 双模式 client。

        gemini Wave 1 review finding Graphiti.1 修复：在已有 event loop 的环境
        （pytest-asyncio / FastAPI / Jupyter / Harness 内部 loop）调 asyncio.run
        会抛 RuntimeError。本实现按优先级降级：
        1. 同步 result：直接返
        2. 有 running loop：用 run_coroutine_threadsafe 投递到当前 loop
        3. 无 loop：asyncio.run 起新 loop
        4. 兜底：新建独立线程跑新 loop（避免 nest_asyncio 依赖）
        """
        if not inspect.isawaitable(result):
            return result

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # 已在 event loop 中（pytest-asyncio / FastAPI / Jupyter）
            # 用独立线程 + 新 loop 兜底（最稳妥，无第三方依赖）
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, result).result()

        return asyncio.run(result)

    def _acquire_provider_token(self, provider: str, tokens: int) -> None:
        if self._rate_limiter is None:
            return
        self._rate_limiter.acquire(provider, tokens=tokens)

    def _record_belongs_to_user(
        self, user_id: str, record: dict[str, Any], source_turn_ids: set[str]
    ) -> bool:
        user_turn_ids = self._turn_ids_by_user.get(user_id, set())
        if not user_turn_ids:
            return False
        if source_turn_ids & user_turn_ids:
            return True
        record_turn_ids = _extract_turn_ids(record)
        if record_turn_ids & user_turn_ids:
            return True
        metadata = record.get("metadata", {})
        if isinstance(metadata, dict):
            session_name = metadata.get("session_name")
            if isinstance(session_name, str) and session_name.startswith(f"u_{user_id}_"):
                return True
        return False

    def _log(self, event: str, **kwargs: Any) -> None:
        if self._logger is None:
            return
        self._logger.info(event, **kwargs)

    @staticmethod
    def _session_name(user_id: str, session_id: str) -> str:
        return f"u_{user_id}_{session_id}"


def _iso_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _clamp_score(score: float) -> float:
    if score < 0:
        return 0.0
    if score > 1:
        return 1.0
    return score


def _split_bulk_payload(payload: Any, expected: int) -> list[Any]:
    if expected <= 0:
        return []
    if isinstance(payload, list):
        if len(payload) == expected:
            return payload
        if len(payload) == 1:
            return payload * expected
    if isinstance(payload, dict):
        for key in ("results", "items", "episodes", "memories"):
            rows = payload.get(key)
            if isinstance(rows, list) and rows:
                if len(rows) == expected:
                    return rows
                if len(rows) == 1:
                    return rows * expected
    return [payload for _ in range(expected)]


def _extract_memory_ids(payload: Any) -> list[str]:
    found: set[str] = set()
    for row in _iter_nodes(payload):
        if isinstance(row, dict):
            for key in ("entity_id", "relation_id", "memory_id", "id", "uuid"):
                value = row.get(key)
                if isinstance(value, str) and value:
                    found.add(value)
            for key in ("entity_ids", "relation_ids", "memory_ids", "ids"):
                values = row.get(key)
                if isinstance(values, list):
                    for value in values:
                        if isinstance(value, str) and value:
                            found.add(value)
    return sorted(found)


def _extract_search_records(payload: Any) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("results", "items", "entities", "relations", "memories"):
            rows = payload.get(key)
            if isinstance(rows, list):
                candidates.extend(rows)
        if not candidates and any(
            key in payload
            for key in (
                "entity_id",
                "relation_id",
                "memory_id",
                "id",
                "uuid",
                "name",
                "description",
                "text",
            )
        ):
            candidates.append(payload)
    elif isinstance(payload, list):
        candidates.extend(payload)
    else:
        for attr in ("results", "entities", "relations", "memories"):
            rows = getattr(payload, attr, None)
            if isinstance(rows, list):
                candidates.extend(rows)
        if not candidates:
            candidates.append(payload)

    records: list[dict[str, Any]] = []
    for idx, row in enumerate(candidates):
        rec = _record_from_row(row, idx)
        if rec is not None:
            records.append(rec)
    return records


def _record_from_row(row: Any, idx: int) -> dict[str, Any] | None:
    data = _to_plain_dict(row)
    if not data:
        return None

    memory_id = _pick_first_str(data, ["entity_id", "relation_id", "memory_id", "id", "uuid"])
    if not memory_id:
        memory_id = f"graphiti_row_{idx}"

    name = _pick_first_str(data, ["name", "title", "entity_name", "subject"])
    detail = _pick_first_str(data, ["description", "summary", "text", "content", "predicate"])
    text = " ".join(piece for piece in [name, detail] if piece).strip() or memory_id

    score = _pick_first_float(data, ["rrf_score", "score", "final_score", "rank_score"], 0.0)
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    timestamp = None
    ts_any = metadata.get("ts", data.get("ts"))
    if isinstance(ts_any, (float, int)):
        timestamp = float(ts_any)

    kind = _pick_first_str(data, ["kind", "type", "label"], default="node")
    return {
        "memory_id": memory_id,
        "text": text,
        "score": score,
        "kind": kind,
        "metadata": metadata,
        "timestamp": timestamp,
        "raw": data,
    }


def _extract_turn_ids(payload: Any) -> set[str]:
    out: set[str] = set()
    for row in _iter_nodes(payload):
        if isinstance(row, dict):
            value = row.get("turn_id")
            if isinstance(value, str) and value:
                out.add(value)
            values = row.get("turn_ids")
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, str) and item:
                        out.add(item)
    return out


def _extract_named_count(payload: Any, name: str) -> int:
    if isinstance(payload, dict):
        value = payload.get(name)
        if isinstance(value, (int, float)):
            return int(value)
        for key in ("stats", "data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                nested_val = nested.get(name)
                if isinstance(nested_val, (int, float)):
                    return int(nested_val)
    return -1


def _iter_nodes(payload: Any) -> Iterable[Any]:
    queue = [payload]
    while queue:
        item = queue.pop(0)
        yield item
        if isinstance(item, dict):
            queue.extend(item.values())
        elif isinstance(item, list):
            queue.extend(item)
        else:
            data = _to_plain_dict(item)
            if data:
                queue.append(data)


def _to_plain_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "model_dump") and callable(row.model_dump):
        dumped = row.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(row, "dict") and callable(row.dict):
        dumped = row.dict()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(row, "__dict__"):
        return {k: v for k, v in vars(row).items() if not k.startswith("_")}
    return {}


def _pick_first_str(data: dict[str, Any], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _pick_first_float(data: dict[str, Any], keys: list[str], default: float) -> float:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return default
