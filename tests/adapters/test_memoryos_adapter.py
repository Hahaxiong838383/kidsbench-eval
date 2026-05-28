from __future__ import annotations

import os
import time
from typing import Any

import pytest

from kidsbench.adapters import MemoryOSAdapter
from kidsbench.contract import ReadOpts, Turn
from kidsbench.middleware import GlobalRateLimiter, RateLimitError, SidecarStore


class FakeMemoryManager:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.short: list[dict[str, Any]] = []
        self.mid: list[dict[str, Any]] = []
        self.long: list[dict[str, Any]] = []
        self.consolidate_calls = 0

    def write(self, user_input: str, system_response: str, metadata: dict[str, Any]) -> dict[str, Any]:
        text = user_input or system_response
        item = {
            "id": f"{self.user_id}_mem_{len(self.short) + 1}",
            "text": text,
            "score": 0.9,
            "metadata": dict(metadata),
            "ts": metadata.get("ts"),
        }
        self.short.append(item)
        return {"id": item["id"], "usage": {"total_tokens": 5}}

    def retrieve(self, query: str, context_window: int) -> list[dict[str, Any]]:
        _ = query
        merged = list(self.short) + list(self.long)
        return list(reversed(merged))[:context_window]

    def consolidate(self) -> dict[str, Any]:
        self.consolidate_calls += 1
        if self.short:
            joined = " | ".join(str(item["text"]) for item in self.short if item.get("text"))
            self.long.append(
                {
                    "id": f"{self.user_id}_ltm_{self.consolidate_calls}",
                    "text": f"summary::{joined}",
                    "score": 0.8,
                    "metadata": {"source": "ltm"},
                }
            )
            self.short.clear()
        return {
            "consolidated_count": len(self.long),
            "usage": {"prompt_tokens": 10, "completion_tokens": 6},
        }

    def reset_all(self) -> None:
        self.short.clear()
        self.mid.clear()
        self.long.clear()


def _turn(turn_id: str, role: str, text: str, ts: float | None = None) -> Turn:
    now = ts if ts is not None else time.time()
    return Turn(
        turn_id=turn_id,
        session_id="s1",
        role=role,  # type: ignore[arg-type]
        text=text,
        timestamp=now,
    )


def _factory(user_id: str, **_: Any) -> FakeMemoryManager:
    return FakeMemoryManager(user_id=user_id)


def test_write_read_clear_consolidate_flow() -> None:
    sidecar = SidecarStore(backend="memory")
    adapter = MemoryOSAdapter(config={"memory_manager_factory": _factory}, sidecar=sidecar)

    adapter.write("u1", _turn("t1", "user", "我叫小明"))
    adapter.write("u1", _turn("t2", "assistant", "你好小明"))
    adapter.flush("u1")
    before = adapter.read("u1", "小明", ReadOpts(top_k=5))
    assert before.memories
    assert all(memory.source_turn_ids for memory in before.memories)

    cstats = adapter.consolidate("u1")
    assert cstats.success
    assert cstats.consolidated_count >= 1
    assert cstats.cost_token >= 1

    after = adapter.read("u1", "summary", ReadOpts(top_k=5))
    assert after.memories
    assert any("summary::" in memory.text for memory in after.memories)

    clear_stats = adapter.clear("u1")
    assert clear_stats.success
    assert adapter.read("u1", "小明", ReadOpts(top_k=5)).memories == []


def test_per_user_manager_isolation() -> None:
    adapter = MemoryOSAdapter(config={"memory_manager_factory": _factory})
    adapter.write("u1", _turn("t1", "user", "u1 only"))
    adapter.write("u2", _turn("t2", "user", "u2 only"))
    adapter.flush("u1")
    adapter.flush("u2")
    read_u1 = adapter.read("u1", "u1", ReadOpts(top_k=3))
    read_u2 = adapter.read("u2", "u2", ReadOpts(top_k=3))
    assert all("u1" in memory.text for memory in read_u1.memories)
    assert all("u2" in memory.text for memory in read_u2.memories)


def test_rate_limiter_exhaustion_raises_rate_limit_error() -> None:
    limiter = GlobalRateLimiter()
    limiter.register("openai", rate=1.0, burst=1)
    assert limiter.try_acquire("openai", tokens=1)

    adapter = MemoryOSAdapter(
        config={"memory_manager_factory": _factory},
        rate_limiter=limiter,
    )
    with pytest.raises(RateLimitError):
        adapter.write("u1", _turn("t1", "user", "hello"))


def test_lane_compatibility_profile() -> None:
    adapter = MemoryOSAdapter(config={"memory_manager_factory": _factory})
    profile = adapter.get_capability_profile()
    assert profile.lane_compatibility["A2"] == "compatible"
    assert profile.lane_compatibility["A1"] == "degraded"
    assert profile.lane_compatibility["A3"] == "incompatible"
    assert profile.lane_compatibility["C"] == "incompatible"


@pytest.mark.integration
def test_integration_memoryos_five_turns_consolidate_retrieve() -> None:
    try:
        from memoryos import MemoryManager  # type: ignore
    except Exception:
        pytest.skip("memoryos-pypi not installed")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    adapter = MemoryOSAdapter(config={"memory_manager_factory": lambda **_: MemoryManager()})
    turns = [
        _turn("t1", "user", "我养了一只猫叫团子"),
        _turn("t2", "assistant", "团子很可爱"),
        _turn("t3", "user", "团子最喜欢冻干"),
        _turn("t4", "assistant", "我记住了"),
        _turn("t5", "user", "帮我回忆团子的习惯"),
    ]
    adapter.batch_write("u_itg", turns)
    adapter.flush("u_itg")
    cstats = adapter.consolidate("u_itg")
    assert cstats.success

    result = adapter.read("u_itg", "团子的习惯", ReadOpts(top_k=5))
    assert result.memories
    hit_count = sum(1 for memory in result.memories if memory.source_turn_ids)
    assert hit_count >= 1
