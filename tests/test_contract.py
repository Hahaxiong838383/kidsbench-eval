"""契约一致性测试。

每个 Adapter 都必须通过这套测试，否则不能进入评测。
跑法：pytest tests/test_contract.py
"""
from __future__ import annotations

import time
from typing import Callable

import pytest

from kidsbench.adapters import FullHistoryAdapter, NoMemoryAdapter, OracleAdapter
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
        store = adapter._store.get(user_id, {})  # noqa: SLF001
        return [tid for tid in candidates if tid in store]

    adapter.set_gold_lookup(lookup)
    return adapter


ADAPTER_FACTORIES: dict[str, Callable[[], MemoryAdapter]] = {
    "nomemory": make_nomemory,
    "fullhistory": make_fullhistory,
    "oracle": make_oracle,
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
