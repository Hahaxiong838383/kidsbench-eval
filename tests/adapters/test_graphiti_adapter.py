from __future__ import annotations

import os
import socket
from datetime import datetime, timezone

import pytest

from kidsbench.adapters.graphiti_adapter import GraphitiAdapter
from kidsbench.contract import AdapterError, ReadOpts, Turn
from kidsbench.middleware import SidecarStore, VirtualClock


def _turn(turn_id: str, text: str, ts: float = 1700000000.0) -> Turn:
    return Turn(
        turn_id=turn_id,
        session_id="s1",
        role="user",
        text=text,
        timestamp=ts,
        metadata={"cognitive_type": "episodic"},
    )


class _FakeClientNoBulk:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.delete_calls: list[str] = []
        self.prov_calls: list[dict[str, str]] = []
        self.flush_calls = 0
        self._search_payload: object = {"items": []}
        self._stats_payload: object = {"node_count": 3, "edge_count": 2, "episode_count": 1}

    async def add_episode(self, name: str, episode_body: str, metadata: dict[str, object]):
        self.add_calls.append({"name": name, "episode_body": episode_body, "metadata": metadata})
        return {"entity_id": f"m_{len(self.add_calls)}"}

    async def search(self, query: str, search_config):
        self.search_calls.append({"query": query, "search_config": search_config})
        return self._search_payload

    async def delete_session(self, name: str):
        self.delete_calls.append(name)
        return {"ok": True}

    async def flush_pending(self):
        self.flush_calls += 1
        return {"ok": True}

    async def query_provenance(self, memory_id: str, user_id: str):
        self.prov_calls.append({"memory_id": memory_id, "user_id": user_id})
        return {"turn_ids": [f"p_{memory_id}"]}

    async def get_stats(self, user_id: str):
        return self._stats_payload


class _FakeClientWithBulk(_FakeClientNoBulk):
    async def add_episode_bulk(self, items: list[dict[str, object]]):
        out: list[dict[str, object]] = []
        for idx, _item in enumerate(items, start=1):
            out.append({"entity_id": f"b_{idx}"})
        return out


@pytest.fixture
def no_avx2_gate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("kidsbench.adapters.graphiti_adapter.check_cpu_avx2", lambda: True)


def _cap_level(adapter: GraphitiAdapter, feature: str) -> str:
    profile = adapter.get_capability_profile()
    for cap in profile.capabilities:
        if cap.feature == feature:
            return cap.level
    raise AssertionError(f"feature missing: {feature}")


def test_write_injects_current_time_and_sidecar(no_avx2_gate):
    client = _FakeClientNoBulk()
    adapter = GraphitiAdapter(config={"client": client}, sidecar=SidecarStore())
    clock = VirtualClock(start=1700000000.0)
    with clock.as_context():
        adapter.write("u1", _turn("t_001", "团子喜欢冻干"))

    assert len(client.add_calls) == 1
    metadata = client.add_calls[0]["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["turn_id"] == "t_001"
    expected = datetime.fromtimestamp(1700000000.0, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    assert str(metadata["current_time"]).startswith(expected.replace("Z", ""))
    assert adapter._sidecar.get_turn_ids("u1", "m_1") == ["t_001"]


def test_read_wraps_search_and_unions_provenance(no_avx2_gate):
    client = _FakeClientNoBulk()
    client._search_payload = {
        "items": [
            {
                "entity_id": "m_1",
                "name": "团子",
                "description": "布偶猫",
                "rrf_score": 0.91,
                "metadata": {"turn_id": "meta_t"},
            },
            {
                "relation_id": "r_1",
                "description": "喜欢吃冻干",
                "score": 0.77,
                "metadata": {},
            },
        ]
    }
    sidecar = SidecarStore()
    sidecar.put("u1", "side_t", ["m_1"])
    adapter = GraphitiAdapter(config={"client": client}, sidecar=sidecar)
    adapter._turn_ids_by_user["u1"] = {"side_t", "meta_t", "p_m_1", "p_r_1"}

    result = adapter.read("u1", "团子", ReadOpts(top_k=5))
    assert len(result.memories) == 2

    first = next(m for m in result.memories if m.memory_id == "m_1")
    assert set(first.source_turn_ids) == {"meta_t", "side_t", "p_m_1"}
    assert first.score == pytest.approx(0.91)

    second = next(m for m in result.memories if m.memory_id == "r_1")
    assert set(second.source_turn_ids) == {"p_r_1"}
    assert second.score == pytest.approx(0.77)


def test_read_skip_provenance_when_disabled(no_avx2_gate):
    client = _FakeClientNoBulk()
    client._search_payload = {"items": [{"entity_id": "m_1", "name": "团子", "score": 0.6}]}
    sidecar = SidecarStore()
    sidecar.put("u1", "t_001", ["m_1"])
    adapter = GraphitiAdapter(config={"client": client}, sidecar=sidecar)
    adapter._turn_ids_by_user["u1"] = {"t_001"}

    result = adapter.read("u1", "团子", ReadOpts(top_k=5, include_provenance=False))
    assert len(result.memories) == 1
    assert result.memories[0].source_turn_ids == ["t_001"]
    assert client.prov_calls == []


def test_clear_waits_and_verifies(no_avx2_gate, monkeypatch: pytest.MonkeyPatch):
    client = _FakeClientNoBulk()
    client._search_payload = {"items": []}
    adapter = GraphitiAdapter(config={"client": client}, sidecar=SidecarStore())
    adapter.write("u1", _turn("t_001", "团子"))

    sleeps: list[float] = []
    monkeypatch.setattr("kidsbench.adapters.graphiti_adapter.time.sleep", lambda sec: sleeps.append(sec))
    stats = adapter.clear("u1")

    assert stats.success
    assert sleeps == [0.5]
    assert client.delete_calls == ["u_u1_s1"]
    assert client.search_calls[-1]["query"] == "u_u1"
    assert adapter._sidecar.stats("u1")["mapping_count"] == 0


def test_batch_write_native_detection(no_avx2_gate):
    client = _FakeClientWithBulk()
    adapter = GraphitiAdapter(config={"client": client}, sidecar=SidecarStore())
    turns = [_turn("t_001", "a"), _turn("t_002", "b"), _turn("t_003", "c")]
    stats = adapter.batch_write("u1", turns)

    assert len(stats) == 3
    assert _cap_level(adapter, "batch_write_native") == "native"


def test_lane_compatibility_declared(no_avx2_gate):
    adapter = GraphitiAdapter(config={"client": _FakeClientNoBulk()})
    profile = adapter.get_capability_profile()
    assert profile.lane_compatibility == {
        "A1": "degraded",
        "A2": "compatible",
        "A3": "incompatible",
        "B": "compatible",
        "C": "incompatible",
    }


def test_avx2_guard_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("kidsbench.adapters.graphiti_adapter.check_cpu_avx2", lambda: False)
    with pytest.raises(AdapterError, match="AVX2"):
        GraphitiAdapter(backend="falkordb", config={"client": _FakeClientNoBulk()})


@pytest.mark.integration
def test_integration_falkordb_graphiti():
    pytest.importorskip("graphiti_core")
    if os.getenv("RUN_GRAPHITI_INTEGRATION") != "1":
        pytest.skip("set RUN_GRAPHITI_INTEGRATION=1 to run")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sock.connect_ex(("127.0.0.1", 6379)) != 0:
            pytest.skip("FalkorDB not listening on 6379")
    finally:
        sock.close()

    try:
        adapter = GraphitiAdapter(backend="falkordb", uri="redis://localhost:6379")
    except AdapterError as err:
        pytest.skip(f"graphiti init unavailable: {err}")

    turns = [
        _turn("t_001", "团子是一只布偶猫"),
        _turn("t_002", "团子喜欢吃冻干"),
        _turn("t_003", "团子晚上会追激光点"),
        _turn("t_004", "我们周末带团子去体检"),
        _turn("t_005", "团子体重 4.5kg"),
    ]
    adapter.batch_write("u_it", turns)
    adapter.flush("u_it")
    result = adapter.read("u_it", "团子喜欢什么", ReadOpts(top_k=10))
    assert isinstance(result.memories, list)
    adapter.clear("u_it")
