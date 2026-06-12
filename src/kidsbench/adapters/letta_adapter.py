"""Letta（letta-ai/letta 0.16.8）的 KidsBench 契约适配。

范式定位：MemGPT 自管理记忆。本 adapter 走 **archival 直插路径**——
绕开 Letta agent 自主决定存什么的不确定性，直接 passage insert/search，
评测可控、溯源最干净（六系统里唯一 native tags 精确 1:1）。
与 memoryos 分层存储构成范式内对照（对抗审「同范式交叉验证」原则）。

接入要点（全部来自 Phase 0 实测，见 docs/LETTA_VERIFIED_FACTS.md）：
1. server 模式：Letta 0.16 只支持 Postgres（pg0 嵌入式），需先跑
   scripts/setup_letta_server.sh 起 server + 注册 deepseek custom provider
2. embedding：embedding_config 直传指向本地 shim（512维 bge-small-zh），
   绕开 provider 注册 + 避开 deepseek 无 embedding endpoint 的 404
3. 溯源 native：passage tags=[turn_id]，search 原样回传 → 精确 1:1
4. write：passages.create(text, tags=[turn_id])；read：passages.search(query)
   返回 Result(content/tags/id)——content 即文本，tags[0] 即 turn_id
5. 回答端：拿 passage 给统一回答模型（harness 层），不用 Letta agent 回答
6. 清场：删 agent（organization 隔离），下题重建
"""

from __future__ import annotations

import time
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
    "httpx.ConnectError": NetworkError,
    "httpx.HTTPStatusError": NetworkError,
    "openai.RateLimitError": RateLimitError,
    "openai.AuthenticationError": AuthError,
}


class LettaAdapter(MemoryAdapter):
    """Translate Letta archival APIs into KidsBench adapter contract."""

    name = "letta"
    paradigm_tags = {
        "representation": "vector+blocks",
        "retrieval": "archival_search",
        "write_policy": "explicit_insert",
        "controller": "memgpt_agent",
        "cognitive": ["episodic", "semantic"],
    }

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        client: Any | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        """config 键（client 为 None 时构造真实 letta_client.Letta）：
        base_url: letta server（默认 http://127.0.0.1:18283）
        model: chat handle（如 openai-proxy/deepseek-v4-flash）
        embedding: {endpoint, model, dim}（指向本地 shim）
        """
        self._config = dict(config or {})
        self._logger = logger or StructuredLogger(self.name)
        self._client = client
        # user_id → letta agent_id（archival 容器，每个 user 一个 agent）
        self._agents: dict[str, str] = {}

    # ------------------------------------------------------------ client

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from letta_client import Letta
            except Exception as err:  # pragma: no cover - 依赖检查
                raise AdapterError(
                    "letta-client not installed; run: pip install letta letta-client"
                ) from err
            self._client = Letta(
                base_url=self._config.get("base_url", "http://127.0.0.1:18283")
            )
        return self._client

    def _embedding_config(self) -> dict[str, Any]:
        emb = self._config.get("embedding", {})
        return {
            "embedding_endpoint_type": "openai",
            "embedding_endpoint": emb.get("endpoint", "http://127.0.0.1:18230/v1"),
            "embedding_model": emb.get("model", "BAAI/bge-small-zh-v1.5"),
            "embedding_dim": emb.get("dim", 512),
            "batch_size": 32,
        }

    def _ensure_agent(self, user_id: str) -> str:
        """每个 user_id 一个 letta agent 作 archival 容器。"""
        if user_id in self._agents:
            return self._agents[user_id]
        client = self._ensure_client()
        agent = client.agents.create(
            model=self._config.get("model", "openai-proxy/deepseek-v4-flash"),
            embedding_config=self._embedding_config(),
            memory_blocks=[
                {"label": "human", "value": ""},
                {"label": "persona", "value": "小可，K12 学习陪伴助手"},
            ],
        )
        self._agents[user_id] = agent.id
        return agent.id

    # ------------------------------------------------------------ contract

    @track_metrics(method="write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        """archival passage 直插，tags=[turn_id] 做 native 溯源。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.perf_counter()
        client = self._ensure_client()
        agent_id = self._ensure_agent(user_id)
        client.agents.passages.create(
            agent_id=agent_id, text=turn.text, tags=[turn.turn_id]
        )
        return WriteStats(
            success=True,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,  # passage insert 含 embedding 调用，letta 不上报 usage
        )

    @track_metrics(method="flush")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def flush(self, user_id: str) -> FlushStats:
        """passage insert 同步落库（含 embedding），无需额外 flush。"""
        return FlushStats(success=True, latency_ms=0.0)

    @track_metrics(method="read")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        """archival search → Result(content/tags/id)。tags[0]=turn_id（native 溯源）。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        opts = opts or ReadOpts()
        t0 = time.perf_counter()
        agent_id = self._agents.get(user_id)
        if agent_id is None:
            return ReadResult(memories=[], latency_ms=(time.perf_counter() - t0) * 1000)
        client = self._ensure_client()
        resp = client.agents.passages.search(
            agent_id=agent_id, query=query, top_k=max(opts.top_k, 5)
        )
        results = getattr(resp, "results", []) or []
        memories: list[Memory] = []
        n = len(results)
        for rank, item in enumerate(results[: opts.top_k]):
            content = getattr(item, "content", "") or ""
            if not content:
                continue
            tags = getattr(item, "tags", None) or []
            # tags 可能是 list 或 str 形态的 list（SDK 序列化差异）
            turn_ids = self._parse_tags(tags)
            memories.append(Memory(
                memory_id=str(getattr(item, "id", "")),
                text=content,
                score=(n - rank) / n if n else 0.0,  # search 无显式分数，排名归一化
                source_turn_ids=turn_ids,
                timestamp=None,
                metadata={},
                provenance_mode="native" if turn_ids else "unknown",
                text_nature="verbatim",  # archival 存原文，非 LLM 抽取
            ))
        return ReadResult(
            memories=memories,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,
            raw={"n_results": n},
        )

    @track_metrics(method="clear")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def clear(self, user_id: str) -> ClearStats:
        """删 agent（archival 容器整体清，organization 隔离），下题重建。"""
        t0 = time.perf_counter()
        deleted = 0
        agent_id = self._agents.pop(user_id, None)
        if agent_id is not None:
            client = self._ensure_client()
            try:
                client.agents.delete(agent_id)
                deleted = 1
            except Exception as err:
                self._logger.warning(f"letta agent delete failed: {str(err)[:80]}")
        return ClearStats(
            success=True,
            latency_ms=(time.perf_counter() - t0) * 1000,
            deleted_count=deleted,
        )

    @track_metrics(method="consolidate")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def consolidate(self, user_id: str) -> ConsolidateStats:
        """archival 无独立 consolidate（passage 即时可检索）。"""
        return ConsolidateStats(success=True, latency_ms=0.0)

    def close(self) -> None:
        # 清理残留 agent（teardown 尽力而为）
        if self._client is not None:
            for agent_id in list(self._agents.values()):
                try:
                    self._client.agents.delete(agent_id)
                except Exception:
                    pass
            self._agents.clear()

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _parse_tags(tags: Any) -> list[str]:
        """tags 可能是 list[str] 或 "['t_001']" 字符串形态——统一成 list。"""
        if isinstance(tags, list):
            return [str(t) for t in tags if t]
        if isinstance(tags, str) and tags.startswith("["):
            import ast

            try:
                parsed = ast.literal_eval(tags)
                return [str(t) for t in parsed if t]
            except (ValueError, SyntaxError):
                return []
        return []

    # ------------------------------------------------------------ 白盒接口

    def get_dependencies(self) -> list:
        from ..contract import Dependency

        return [
            Dependency(
                "letta-server", "service", required=True,
                check_hint="bash scripts/setup_letta_server.sh（pg0 + deepseek provider）",
                swap_supported=False,
            ),
            Dependency(
                self._config.get("model", "unknown"), "internal_llm", required=False,
                check_hint="archival 直插路径不调 agent LLM（回答走统一模型）",
                swap_supported=True, config_key="model",
                actual_model=self._config.get("model"),
            ),
            Dependency(
                "bge-small-zh-v1.5(shim)", "embedding", required=True,
                check_hint="embedding_config 指向本地 shim（512维对齐评测标准）",
                swap_supported=True, config_key="embedding",
            ),
        ]

    def get_stats(self, user_id: str) -> dict[str, Any]:
        from ..middleware import METRICS

        return {
            "agent_id": self._agents.get(user_id),
            "active_agents": len(self._agents),
            "metrics": METRICS.snapshot(self.name, user_id),
        }

    def get_capability_profile(self):
        from ..contract import STANDARD_FEATURES, Capability, CapabilityProfile

        caps_map = {
            "physical_clear": ("native", "删 agent 整体清 archival（organization 隔离，实测）"),
            "turn_id_traceback": ("native", "passage tags=[turn_id] 原样回传（实测 ['t_001']）——六系统最干净 1:1"),
            "cognitive_type_filter": ("declared", "archival 不分认知类型；memory_blocks 是 human/persona"),
            "score_normalized": ("computed", "search 无显式分数，排名归一化 [0,1]"),
            "concurrent_safe": ("native", "每 user 独立 agent，organization 级隔离"),
            "cost_accounting": ("declared", "letta 不上报 passage embedding usage"),
            "embedding_export": ("native", "passage 自带 embedding 字段"),
            "flush_blocking": ("native", "passage insert 同步落库，立即可检索"),
            "consolidate_explicit": ("declared", "archival 无 consolidate，即时检索"),
            "batch_write_native": ("declared", "逐条 passage create（保 1:1 tags 溯源）"),
            "write_semantic_sync": ("native", "create 同步含 embedding"),
            "lineage_after_consolidate": ("native", "无 consolidate，tags 永不丢"),
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
                                "A3": "compatible", "B": "compatible", "C": "compatible"},
            lane_notes={
                "C": "archival 直插路径纯检索（不调 agent LLM），C lane 兼容",
            },
        )
