"""Cognee（topoteretes/cognee 0.5.1）的 KidsBench 契约适配。

范式定位：**pipeline 双库多跳（vector seed + k-hop 邻域投影）**——raw→实体抽取→
关系图→向量化双库，检索时向量 seed top-k 出发做 k-hop PageRank 风格邻域投影。
与 graphiti（temporal KG）构成范式内对照。主场 = 多跳联想/干扰召回。

接入要点（全部来自 Phase 0 实测，见 docs/COGNEE_VERIFIED_FACTS.md）：
1. 部署：kuzu（嵌入式图库）+ LanceDB（本地向量库）+ SQLite 元数据，零外部服务
2. ⚠️ instructor 模式上游 quirk：LLM_INSTRUCTOR_MODE 只在模型名含 gpt-5 时生效，
   其他模型走 TOOLS 模式 → deepseek thinking 拒 tool_choice / gemini proxy malformed
   → 必须 import cognee 前 monkey patch instructor.from_litellm 强制 JSON 模式
3. 中文：默认英文 few-shot prompt 抽英文实体（53%）→ cognify(custom_prompt=ZH_PROMPT)
   强制中文（实测 100%）
4. 异步 API：cognee.add/cognify/search/prune 全 async → adapter 用 asyncio.run 包同步
5. 溯源最弱（wrapped）+ 虚拟时钟 declared 受限——能力矩阵如实标，Attribution/时序题失分
6. 清场：prune_data+prune_system(metadata) 全局物理删（无 dataset 局部 prune，评测逐题全清）

⚠️ 已知约束（codex 对抗审记录，评测协议下可接受，生产需另设计）：
- instructor.from_litellm 的 monkey patch 是进程级全局副作用（强制 JSON 模式）。本进程内
  只有 cognee 用 instructor，故无冲突；若同进程跑其他依赖 instructor TOOLS 模式的代码会受影响。
- prune 全局清 + _seen 进程内查重：评测协议串行单进程、逐题全清重建，隔离/幂等成立；
  多 worker / 跨实例 / 重启场景不成立（concurrent_safe + lineage 已 declared 标明）。
"""

from __future__ import annotations

import asyncio
import os
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
    "openai.RateLimitError": RateLimitError,
    "openai.AuthenticationError": AuthError,
}

# 中文实体抽取铁律 prompt（Phase 0 A/B 实测：默认 53% → 此 prompt 100% 中文）
_ZH_PROMPT = """你是知识图谱抽取专家。从用户文本中抽取实体和关系，构建知识图谱。
铁律：所有实体名、关系名必须使用与原文相同的语言（中文文本输出中文实体），
绝对禁止把实体翻译成英文。实体名保持原文表述（如「团子」「小川」「数学期中考试」）。
按指定的 JSON schema 输出。"""

_PATCHED = False


def _apply_instructor_patch() -> None:
    """强制 instructor JSON 模式（必须在 import cognee 前）。
    上游 quirk：instructor_mode 配置只在模型名含 gpt-5 时生效（openai/adapter.py），
    其他模型走 TOOLS 模式 → deepseek thinking 拒 tool_choice / gemini proxy malformed。"""
    global _PATCHED
    if _PATCHED:
        return
    import instructor

    _orig = instructor.from_litellm

    def _patched(fn, mode=None, **kw):
        return _orig(fn, mode=instructor.Mode.JSON, **kw)

    instructor.from_litellm = _patched
    _PATCHED = True


class CogneeAdapter(MemoryAdapter):
    """Translate Cognee pipeline+multihop APIs into KidsBench adapter contract."""

    name = "cognee"
    paradigm_tags = {
        "representation": "vector+knowledge_graph",
        "retrieval": "multihop_neighborhood",
        "write_policy": "pipeline_extraction",
        "controller": "rule",
        "cognitive": ["semantic", "episodic"],
    }

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        cognee_module: Any | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        """config 键（默认指向 deepseek/gemini proxy + 本地 shim）：
        llm_endpoint/llm_model/llm_api_key, embedding_endpoint/embedding_api_key,
        neighborhood_depth（多跳深度，默认 2）
        cognee_module: 注入 mock（测试用），None 时 import 真 cognee
        """
        self._config = dict(config or {})
        self._logger = logger or StructuredLogger(self.name)
        self._cognee = cognee_module
        self._search_type = None
        # 每 user 一个 dataset；content hash 查重（cognee 非幂等）
        self._seen: dict[str, set[str]] = {}
        self._stats = {"total_writes": 0, "total_reads": 0, "dedup_hits": 0}
        self._setup_done = False
        self._loop: Any = None  # 持久 event loop（见 _run docstring）

    # ------------------------------------------------------------ cognee

    def _ensure_cognee(self) -> Any:
        if self._cognee is not None:
            return self._cognee
        # 注入环境（import 前）
        for k, v in self._env_from_config().items():
            os.environ.setdefault(k, v)
        _apply_instructor_patch()
        try:
            import cognee
            from cognee.modules.search.types import SearchType
        except Exception as err:  # pragma: no cover - 依赖检查
            raise AdapterError(
                'cognee not installed; run: pip install cognee "mistralai>=1.5,<2"'
            ) from err
        self._cognee = cognee
        self._search_type = SearchType
        return self._cognee

    def _env_from_config(self) -> dict[str, str]:
        c = self._config
        env = {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": c.get("llm_model", "openai/gemini-2.5-flash"),
            "LLM_ENDPOINT": c.get("llm_endpoint", "http://23.226.135.149:4000/v1"),
            "LLM_INSTRUCTOR_MODE": "json_mode",
            "EMBEDDING_PROVIDER": "custom",  # 非 openai：避开 tiktoken 模型名映射 KeyError
            "EMBEDDING_MODEL": c.get("embedding_model", "openai/BAAI/bge-small-zh-v1.5"),
            "EMBEDDING_ENDPOINT": c.get("embedding_endpoint", "http://127.0.0.1:18230/v1"),
            "EMBEDDING_API_KEY": c.get("embedding_api_key", "dummy-local"),
            "EMBEDDING_DIMENSIONS": str(c.get("embedding_dim", 512)),
            "GRAPH_DATABASE_PROVIDER": "kuzu",
            "VECTOR_DB_PROVIDER": "lancedb",
            "ENABLE_BACKEND_ACCESS_CONTROL": "false",
        }
        if c.get("llm_api_key"):
            env["LLM_API_KEY"] = c["llm_api_key"]
        return env

    def _run(self, coro: Any) -> Any:
        """async cognee API → 同步契约。**持久事件循环**（per adapter 实例）。

        不能用 asyncio.run（每次新建 loop）：cognee 持有模块级 asyncio.Lock，
        绑定首个 loop，第二次调用在新 loop 里炸 "bound to a different event loop"
        （w3 smoke 实战，codex 对抗审 P2 预警命中）。"""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def _dataset(self, user_id: str) -> str:
        return f"kb_{user_id}"

    # ------------------------------------------------------------ contract

    @track_metrics(method="write")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        """add(text, dataset, node_set=[turn_id])。建图延迟到 consolidate（cognify）。

        cognify 慢（每次多次 LLM 调用），不在 write 时逐条建图——攒到 consolidate 批量。
        content hash 查重防 cognee 非幂等（重复 add 产生重复 chunk）。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.perf_counter()
        cognee = self._ensure_cognee()
        seen = self._seen.setdefault(user_id, set())
        # 按 turn_id 查重（codex 对抗审 P2：用 content hash 会把「同文本不同 turn」误去重，
        # 破坏时序/溯源题；turn_id 才是重试幂等的正确键，与 MemMachine/Memobase 一致）
        if turn.turn_id in seen:
            self._stats["dedup_hits"] += 1
            return WriteStats(
                success=True, latency_ms=(time.perf_counter() - t0) * 1000,
                cost_token=0, raw={"deduplicated": True},
            )
        self._run(cognee.add(turn.text, self._dataset(user_id), node_set=[turn.turn_id]))
        seen.add(turn.turn_id)
        self._stats["total_writes"] += 1
        return WriteStats(
            success=True, latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0, raw={"staged": True},  # 仅入库，建图在 consolidate
        )

    @track_metrics(method="flush")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def flush(self, user_id: str) -> FlushStats:
        """add 即时入库；建图（cognify）是 consolidate 的活，flush no-op。"""
        return FlushStats(success=True, latency_ms=0.0)

    @track_metrics(method="consolidate")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def consolidate(self, user_id: str) -> ConsolidateStats:
        """cognify(custom_prompt=ZH_PROMPT)：批量建知识图谱（强制中文实体）。

        这是 pipeline 的重活（多次 LLM 调用，慢）——契约把它放 consolidate
        而非 flush，harness 在 batch_write 后显式触发。"""
        if user_id not in self._seen or not self._seen[user_id]:
            return ConsolidateStats(success=True, latency_ms=0.0)
        t0 = time.perf_counter()
        cognee = self._ensure_cognee()
        self._run(cognee.cognify([self._dataset(user_id)], custom_prompt=_ZH_PROMPT))
        return ConsolidateStats(
            success=True, latency_ms=(time.perf_counter() - t0) * 1000,
            consolidated_count=len(self._seen[user_id]),
            consolidation_phase="graph_build",
        )

    @track_metrics(method="read")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        """search(GRAPH_COMPLETION, neighborhood_depth=k)：多跳邻域投影回答。

        ⚠️ GRAPH_COMPLETION 返回 LLM 合成文本，无源引用——溯源 wrapped（最弱项）。"""
        if not user_id:
            raise AdapterError("user_id must not be empty")
        opts = opts or ReadOpts()
        t0 = time.perf_counter()
        if user_id not in self._seen:
            return ReadResult(memories=[], latency_ms=(time.perf_counter() - t0) * 1000)
        cognee = self._ensure_cognee()
        # 多跳深度参数按版本适配：neighborhood_depth 是 main 分支新 API，
        # PyPI 0.5.1 没有（真 server 直测抓出的版本错位）——0.5.1 的多跳由
        # GRAPH_COMPLETION 内置图遍历 + wide_search_top_k 承担
        import inspect

        search_params = inspect.signature(cognee.search).parameters
        kwargs: dict[str, Any] = {
            "query_type": self._search_type.GRAPH_COMPLETION,
            "top_k": max(opts.top_k, 5),
            "datasets": [self._dataset(user_id)],
        }
        depth = int(self._config.get("neighborhood_depth", 2))
        if "neighborhood_depth" in search_params:
            kwargs["neighborhood_depth"] = depth
        results = self._run(cognee.search(query, **kwargs))
        self._stats["total_reads"] += 1
        memories: list[Memory] = []
        for rank, r in enumerate(results[: opts.top_k]):
            text = str(r)
            if not text:
                continue
            memories.append(Memory(
                memory_id=f"graph_{rank}",
                text=text,
                score=1.0 - rank * 0.05,  # 无显式分数，按返回序
                source_turn_ids=[],  # GRAPH_COMPLETION 合成文本无源引用（wrapped）
                timestamp=None,
                metadata={"kind": "graph_completion", "depth": depth},
                provenance_mode="wrapped",
                text_nature="synthesized",  # LLM 多跳合成
            ))
        return ReadResult(
            memories=memories,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_token=0,
            raw={"n_results": len(results), "depth": depth},
        )

    @track_metrics(method="clear")
    @wrap_errors(mapping=_ERROR_MAPPING)
    def clear(self, user_id: str) -> ClearStats:
        """prune 全局物理删（无 dataset 局部 prune；评测协议逐题全清+重建）。"""
        t0 = time.perf_counter()
        n_seen = len(self._seen.pop(user_id, set()))
        cognee = self._ensure_cognee()
        self._run(cognee.prune.prune_data())
        self._run(cognee.prune.prune_system(metadata=True))
        return ClearStats(
            success=True, latency_ms=(time.perf_counter() - t0) * 1000,
            deleted_count=n_seen,
        )

    def close(self) -> None:
        """teardown：关闭持久 event loop（尽力而为）。"""
        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    # ------------------------------------------------------------ 白盒接口

    def get_dependencies(self) -> list:
        from ..contract import Dependency

        return [
            Dependency(
                "cognee", "api", required=True,
                check_hint='pip install cognee "mistralai>=1.5,<2"（mistralai 2.x 炸 import）',
                swap_supported=False,
            ),
            Dependency(
                self._config.get("llm_model", "gemini-2.5-flash"), "internal_llm", required=True,
                check_hint="env LLM_*（实体抽取+多跳合成都调，JSON 模式 monkey patch 必须）",
                swap_supported=True, config_key="llm_model",
            ),
            Dependency(
                "bge-small-zh-v1.5(shim)", "embedding", required=True,
                check_hint="env EMBEDDING_*（provider=custom 避 tiktoken KeyError）",
                swap_supported=True, config_key="embedding_endpoint",
            ),
        ]

    def get_stats(self, user_id: str) -> dict[str, Any]:
        from ..middleware import METRICS

        return {
            "active_datasets": len(self._seen),
            "seen_chunks": len(self._seen.get(user_id, set())),
            **self._stats,
            "metrics": METRICS.snapshot(self.name, user_id),
        }

    def get_injected_providers(self) -> dict[str, str]:
        return {
            "internal_llm": self._config.get("llm_model", "gemini-2.5-flash"),
            "internal_embed": "bge-small-zh-v1.5",
        }

    def get_capability_profile(self):
        from ..contract import STANDARD_FEATURES, Capability, CapabilityProfile

        caps_map = {
            "physical_clear": ("native", "prune_data+prune_system(metadata) 全局物理删（删后 search 抛 DatabaseNotCreated）"),
            "turn_id_traceback": ("wrapped", "⚠️ 最弱项：GRAPH_COMPLETION 合成文本无源引用，仅 node_set=turn 批次标记"),
            "cognitive_type_filter": ("declared", "图谱不分认知类型"),
            "score_normalized": ("computed", "GRAPH_COMPLETION 无显式分数，按返回序归一化"),
            "concurrent_safe": ("declared", "⚠️ 单用户：prune 全局清，无 dataset 局部隔离——评测逐题全清重建保隔离"),
            "cost_accounting": ("simulated", "session usage char/4 估算（非精确）"),
            "embedding_export": ("declared", "LanceDB 内部向量，无导出 API"),
            "flush_blocking": ("native", "add 即时入库；建图在 consolidate"),
            "consolidate_explicit": ("native", "cognify 显式建图 pipeline（custom_prompt 中文强制）"),
            "batch_write_native": ("wrapped", "add 逐条+node_set；cognify 批量建图"),
            "write_semantic_sync": ("declared", "⚠️ write 仅入库，图谱要 consolidate 后才可 read——harness 必须 write→consolidate→read"),
            "lineage_after_consolidate": ("declared", "⚠️ 虚拟时钟无注入口+合成无源引用——时序/Attribution 题如实失分"),
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
                "C": "实体抽取+多跳合成强依赖 LLM，C lane 不兼容",
                "write_semantic_sync": "图谱在 consolidate(cognify) 才就位，harness 必须 write→consolidate→read",
                "concurrent_safe": "prune 全局；评测协议逐题全清重建保证隔离",
            },
        )
