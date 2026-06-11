"""契约一致性测试。

每个 Adapter 都必须通过这套测试，否则不能进入评测。
跑法：pytest tests/test_contract.py
"""
from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from kidsbench.adapters import (
    FullHistoryAdapter,
    GraphitiAdapter,
    HindsightAdapter,
    Mem0Adapter,
    MemoryOSAdapter,
    NoMemoryAdapter,
    OracleAdapter,
)
from kidsbench.contract import (
    STANDARD_FEATURES,
    AdapterError,
    MemoryAdapter,
    ReadOpts,
    Turn,
)

# ---- 测试用例工厂 ----

def make_turns(session_id: str = "s1") -> list[Turn]:
    base = time.time()
    return [
        Turn(
            turn_id="t_001",
            session_id=session_id,
            role="user",
            text="我们家有只布偶猫叫团子",
            timestamp=base,
            metadata={"cognitive_type": "episodic"},
        ),
        Turn(
            turn_id="t_002",
            session_id=session_id,
            role="assistant",
            text="布偶猫很温顺呢",
            timestamp=base + 1,
            metadata={"cognitive_type": "semantic"},
        ),
        Turn(
            turn_id="t_003",
            session_id=session_id,
            role="user",
            text="团子最近喜欢吃冻干",
            timestamp=base + 2,
            metadata={"cognitive_type": "episodic"},
        ),
    ]


# ---- Adapter factories（每个 adapter 一个工厂） ----

def make_nomemory() -> NoMemoryAdapter:
    return NoMemoryAdapter()


def make_fullhistory() -> FullHistoryAdapter:
    return FullHistoryAdapter()


def make_oracle() -> OracleAdapter:
    """通用契约测试用的 Oracle：lookup 跟随 store 状态，clear/隔离行为符合通用预期。

    关键设计：lookup 只返回 store 里实际存在的 turn_id —— 这样 clear 后 read 自动返空，
    u2 没写时 read 也自动返空，符合通用契约。
    Oracle 专项测试（TestOracleSpecific）里再单独验证「gold 引用空 turn 必抛错」。
    """
    adapter = OracleAdapter()

    def lookup(user_id: str, query: str) -> list[str]:
        candidates = ["t_001", "t_003"] if "团子" in query else []
        store = adapter._store.get(user_id, {})
        return [tid for tid in candidates if tid in store]

    adapter.set_gold_lookup(lookup)
    return adapter


# ---- Wave 1 第三方 adapter 的契约 mock 工厂 ----

class _ContractFakeMem0Client:
    """Mem0 mock client，用于契约测试不需要装 mem0ai。"""

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}
        self._counter = 0

    def add(self, *, messages, user_id, metadata=None):
        self._counter += 1
        mem_id = f"m_{self._counter:03d}"
        text = messages[0]["content"] if isinstance(messages, list) else str(messages)
        self._store.setdefault(user_id, []).append(
            {"id": mem_id, "memory": text, "score": 0.9, "metadata": dict(metadata or {})}
        )
        return {"results": [{"id": mem_id, "event": "ADD"}]}

    def search(self, *, query, user_id, limit=5):
        rows = self._store.get(user_id, [])
        filtered = [r for r in rows if query and query in r.get("memory", "")]
        if not filtered:
            filtered = list(rows)
        return filtered[:limit]

    def delete_all(self, *, user_id):
        self._store[user_id] = []

    def get_all(self, *, user_id, limit=1):
        return self._store.get(user_id, [])[:limit]


def make_mem0() -> Mem0Adapter:
    """契约测试用 Mock Mem0 client，不需要装 mem0ai。"""
    client = _ContractFakeMem0Client()

    class _ContractMem0Adapter(Mem0Adapter):
        @staticmethod
        def _create_client(config):
            return client

    return _ContractMem0Adapter(disable_telemetry=True)


class _ContractFakeMemoryManager:
    """MemoryOS mock manager，用于契约测试。"""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._items: list[dict] = []
        self.short = self._items
        self.mid: list = []
        self.long: list = []

    def write(self, user_input: str, system_response: str, metadata: dict):
        content = user_input or system_response
        item = {
            "id": f"{self.user_id}_m_{len(self._items) + 1}",
            "text": content,
            "score": 1.0,
            "metadata": dict(metadata),
            "ts": metadata.get("ts"),
        }
        self._items.append(item)
        return {"id": item["id"]}

    def retrieve(self, query: str, context_window: int):
        _ = query
        return list(reversed(self._items))[:context_window]

    def consolidate(self):
        return {"consolidated_count": len(self._items), "usage": {"total_tokens": 8}}

    def reset_all(self) -> None:
        self._items.clear()


def make_memoryos() -> MemoryOSAdapter:
    """契约测试用 Mock MemoryManager 工厂。"""
    return MemoryOSAdapter(
        config={"memory_manager_factory": lambda user_id, **_: _ContractFakeMemoryManager(user_id)}
    )


class _GraphitiContractClient:
    """Graphiti mock client（async），用于契约测试。"""

    def __init__(self) -> None:
        self._sessions: dict[str, list[dict]] = {}
        self._counter = 0

    async def add_episode(self, name: str, episode_body: str, metadata: dict):
        self._counter += 1
        memory_id = f"g_{self._counter:03d}"
        self._sessions.setdefault(name, []).append(
            {"id": memory_id, "text": episode_body, "metadata": dict(metadata)}
        )
        return {"entity_id": memory_id}

    async def search(self, query: str, search_config):
        items: list[dict] = []
        for rows in self._sessions.values():
            for row in rows:
                text = str(row["text"])
                if query and query not in text:
                    continue
                items.append(
                    {
                        "entity_id": str(row["id"]),
                        "name": text,
                        "rrf_score": 0.9,
                        "metadata": dict(row["metadata"]),
                    }
                )
        return {"items": items}

    async def delete_session(self, name: str):
        self._sessions.pop(name, None)
        return {"ok": True}

    async def flush_pending(self):
        return {"ok": True}

    async def get_stats(self, user_id: str):
        node_count = sum(len(rows) for rows in self._sessions.values())
        return {"node_count": node_count, "edge_count": node_count * 2, "episode_count": node_count}


def make_graphiti() -> GraphitiAdapter:
    """契约测试用 Mock Graphiti client（backend=neo4j 跳过 AVX2 检测）。"""
    return GraphitiAdapter(
        backend="neo4j",
        uri="bolt://localhost:7687",
        config={"client": _GraphitiContractClient()},
    )


class _HindsightContractClient:
    """契约测试用 Mock Hindsight client（镜像 hindsight_client.Hindsight 0.8.1 签名）。

    行为对齐 Phase 0 实测（docs/HINDSIGHT_VERIFIED_FACTS.md）：retain 同步、
    metadata 透传、delete_bank 级联、reflect 纯读返回 text+based_on+usage。
    """

    def __init__(self) -> None:
        self._banks: dict[str, list[dict]] = {}
        self._counter = 0

    def retain(self, bank_id: str, content: str, timestamp=None, metadata=None, **kw):
        self._counter += 1
        self._banks.setdefault(bank_id, []).append(
            {
                "id": f"hs_{self._counter:03d}",
                "text": content,
                "type": "world",
                "metadata": dict(metadata or {}),
                "document_id": f"doc_{self._counter:03d}",
                "chunk_id": f"{bank_id}_{self._counter:03d}_0",
                "source_fact_ids": None,
            }
        )
        return {
            "success": True,
            "items_count": 1,
            "usage": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
        }

    def recall(self, bank_id: str, query: str, query_timestamp=None, **kw):
        return {"results": list(self._banks.get(bank_id, []))}

    def reflect(self, bank_id: str, query: str, include_facts: bool = False, **kw):
        facts = list(self._banks.get(bank_id, [])) if include_facts else []
        return {
            "text": "基于现有记忆的合成分析" if self._banks.get(bank_id) else "",
            "based_on": [
                {"id": f["id"], "text": f["text"], "type": f["type"]} for f in facts
            ],
            "usage": {"input_tokens": 200, "output_tokens": 60, "total_tokens": 260},
        }

    def delete_bank(self, bank_id: str):
        self._banks.pop(bank_id, None)
        return {"bank_deleted": True}

    def trigger_consolidation(self, bank_id: str):
        return {"consolidated_count": 0, "usage": {"total_tokens": 0}}

    def close(self) -> None:
        return None


def make_hindsight_recall() -> HindsightAdapter:
    return HindsightAdapter(mode="recall", client=_HindsightContractClient())


def make_hindsight_reflect() -> HindsightAdapter:
    return HindsightAdapter(mode="reflect", client=_HindsightContractClient())


ADAPTER_FACTORIES: dict[str, Callable[[], MemoryAdapter]] = {
    "nomemory": make_nomemory,
    "fullhistory": make_fullhistory,
    "oracle": make_oracle,
    "mem0": make_mem0,
    "memoryos": make_memoryos,
    "graphiti": make_graphiti,
    "hindsight-recall": make_hindsight_recall,
    "hindsight-reflect": make_hindsight_reflect,
}


# ---- 通用契约测试（每个 adapter 都必跑） ----

@pytest.mark.parametrize("adapter_name", list(ADAPTER_FACTORIES.keys()))
class TestContract:
    """所有 Adapter 必须通过的契约测试。"""

    def test_metadata_set(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        assert adapter.name, f"{adapter_name}: name must be set"
        assert adapter.paradigm_tags, f"{adapter_name}: paradigm_tags must be set"

    def test_write_returns_stats(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        turn = make_turns()[0]
        stats = adapter.write("u_test", turn)
        assert stats.success
        assert stats.latency_ms >= 0

    def test_write_empty_user_id_raises(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        turn = make_turns()[0]
        with pytest.raises(AdapterError):
            adapter.write("", turn)

    def test_read_returns_result(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        for turn in make_turns():
            adapter.write("u_test", turn)
        adapter.flush("u_test")
        result = adapter.read("u_test", "团子是什么猫", ReadOpts(top_k=5))
        # memories 是 list（可空，nomemory 就是空）
        assert isinstance(result.memories, list)
        assert result.latency_ms >= 0

    def test_clear_then_read_empty(self, adapter_name: str) -> None:
        """清场锚点：clear 后立刻 read 必须空（防幽灵记忆残留）。"""
        adapter = ADAPTER_FACTORIES[adapter_name]()
        for turn in make_turns():
            adapter.write("u_test", turn)
        adapter.flush("u_test")
        adapter.clear("u_test")
        # clear 后立即 read（不写入新数据）
        result = adapter.read("u_test", "团子", ReadOpts(top_k=5))
        assert result.memories == [], (
            f"{adapter_name}: clear 后 read 必须返回空 list，实际 {len(result.memories)} 条 → "
            "可能是软删/异步删/未真删，存在幽灵记忆残留风险"
        )

    def test_clear_returns_stats(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        for turn in make_turns():
            adapter.write("u_test", turn)
        stats = adapter.clear("u_test")
        assert stats.success
        assert stats.deleted_count >= 0

    def test_flush_returns_stats(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        stats = adapter.flush("u_test")
        assert stats.success

    def test_get_dependencies_returns_list(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        deps = adapter.get_dependencies()
        assert isinstance(deps, list)

    def test_get_stats_returns_dict(self, adapter_name: str) -> None:
        adapter = ADAPTER_FACTORIES[adapter_name]()
        stats = adapter.get_stats("u_test")
        assert isinstance(stats, dict)

    def test_capability_profile_complete(self, adapter_name: str) -> None:
        """capability_profile 必须覆盖所有 STANDARD_FEATURES（防遗漏声明）。"""
        adapter = ADAPTER_FACTORIES[adapter_name]()
        profile = adapter.get_capability_profile()
        assert profile.adapter_name == adapter.name
        declared = {c.feature for c in profile.capabilities}
        missing = set(STANDARD_FEATURES) - declared
        assert not missing, (
            f"{adapter_name}: capability_profile 缺少声明 {missing}，"
            "必须显式声明（哪怕是 unsupported）"
        )

    def test_lane_compatibility_declared(self, adapter_name: str) -> None:
        """capability_profile 必须声明全部 5 档 Lane 适配性（A1/A2/A3/B/C）。"""
        adapter = ADAPTER_FACTORIES[adapter_name]()
        profile = adapter.get_capability_profile()
        for lane in ("A1", "A2", "A3", "B", "C"):
            assert lane in profile.lane_compatibility, (
                f"{adapter_name}: lane_compatibility 缺 '{lane}'，必须声明 compatible/incompatible/degraded"
            )
            assert profile.lane_compatibility[lane] in ("compatible", "incompatible", "degraded"), (
                f"{adapter_name}: lane_compatibility[{lane}] 取值非法"
            )

    def test_batch_write_works(self, adapter_name: str) -> None:
        """batch_write 默认实现必须工作（循环调 write）。"""
        adapter = ADAPTER_FACTORIES[adapter_name]()
        turns = make_turns()
        results = adapter.batch_write("u_test", turns)
        assert len(results) == len(turns)
        assert all(r.success for r in results)

    def test_consolidate_returns_stats(self, adapter_name: str) -> None:
        """consolidate 必须返回 ConsolidateStats（默认 no-op 也算）。"""
        adapter = ADAPTER_FACTORIES[adapter_name]()
        stats = adapter.consolidate("u_test")
        assert stats.success
        assert stats.latency_ms >= 0
        assert stats.consolidated_count >= 0

    def test_concurrent_user_isolation(self, adapter_name: str) -> None:
        """user_id 隔离锚点：u1 的数据不能污染 u2 的召回。"""
        adapter = ADAPTER_FACTORIES[adapter_name]()

        for turn in make_turns():
            adapter.write("u1", turn)
        adapter.flush("u1")
        adapter.flush("u2")

        # u2 没写过任何东西，read 应该返回空（Oracle 的 lookup 也跟随 store 自动返空）
        result_u2 = adapter.read("u2", "团子", ReadOpts(top_k=5))
        assert result_u2.memories == [], (
            f"{adapter_name}: u2 没写数据，read 应该返回空，实际 {len(result_u2.memories)} 条 → user_id 隔离失效"
        )


# ---- 基线特有行为测试 ----

class TestNoMemorySpecific:
    """NoMemory 必须永远返回空。"""

    def test_read_always_empty(self) -> None:
        adapter = NoMemoryAdapter()
        for turn in make_turns():
            adapter.write("u_test", turn)
        adapter.flush("u_test")
        result = adapter.read("u_test", "团子", ReadOpts(top_k=5))
        assert result.memories == [], "NoMemory 必须永远返回空 memories"


class TestFullHistorySpecific:
    """FullHistory 必须按时间序返回全部写入的 turn。"""

    def test_read_returns_all_in_order(self) -> None:
        adapter = FullHistoryAdapter()
        turns = make_turns()
        for turn in turns:
            adapter.write("u_test", turn)
        adapter.flush("u_test")
        result = adapter.read("u_test", "任意 query", ReadOpts(top_k=999))
        assert len(result.memories) == len(turns)
        # 时间顺序
        ts_list = [m.timestamp for m in result.memories]
        assert ts_list == sorted(ts_list)
        # turn_id 完整保留
        returned_ids = {m.source_turn_ids[0] for m in result.memories}
        expected_ids = {t.turn_id for t in turns}
        assert returned_ids == expected_ids


class TestOracleSpecific:
    """Oracle 必须根据 gold_lookup 完美召回。"""

    def test_perfect_recall(self) -> None:
        adapter = OracleAdapter()
        adapter.set_gold_lookup(lambda uid, q: ["t_001", "t_003"])
        for turn in make_turns():
            adapter.write("u_test", turn)
        adapter.flush("u_test")
        result = adapter.read("u_test", "团子", ReadOpts(top_k=5))
        assert len(result.memories) == 2
        ids = {m.source_turn_ids[0] for m in result.memories}
        assert ids == {"t_001", "t_003"}

    def test_no_gold_lookup_raises(self) -> None:
        adapter = OracleAdapter()  # 不设 lookup
        for turn in make_turns():
            adapter.write("u_test", turn)
        with pytest.raises(AdapterError, match="gold_lookup"):
            adapter.read("u_test", "团子", ReadOpts(top_k=5))

    def test_gold_pointing_to_missing_turn_raises(self) -> None:
        """gold 引用了不存在的 turn 必须抛错（暴露题库 bug）。"""
        adapter = OracleAdapter()
        adapter.set_gold_lookup(lambda uid, q: ["t_999"])  # 不存在的 id
        adapter.write("u_test", make_turns()[0])
        with pytest.raises(AdapterError, match="not found"):
            adapter.read("u_test", "任意", ReadOpts(top_k=5))
