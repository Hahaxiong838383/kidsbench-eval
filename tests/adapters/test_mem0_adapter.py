from __future__ import annotations

import importlib.util
import os
import time
from typing import Any

import pytest

from kidsbench.adapters.mem0_adapter import Mem0Adapter
from kidsbench.contract import ReadOpts, Turn
from kidsbench.middleware import EmbeddingService, SidecarStore


class _FakeEmbeddingService(EmbeddingService):
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[float(len(t)), 1.0] for t in texts]

    def dim(self) -> int:
        return 2


class _FakeMem0Client:
    def __init__(self) -> None:
        self._seq = 0
        self._store: dict[str, list[dict[str, Any]]] = {}

    def add(self, *, messages: list[dict[str, str]], user_id: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        bucket = self._store.setdefault(user_id, [])
        created: list[dict[str, Any]] = []
        now = time.time()
        for message in messages:
            self._seq += 1
            item = {
                "id": f"m_{self._seq:03d}",
                "memory": message.get("content", ""),
                "score": 1.0,
                "metadata": dict(metadata),
                "created_at": now,
            }
            bucket.append(item)
            created.append(item)
        return created

    def search(self, *, query: str, user_id: str, limit: int) -> list[dict[str, Any]]:
        rows = self._store.get(user_id, [])
        filtered = [r for r in rows if query in r.get("memory", "")]
        if not filtered:
            filtered = list(rows)
        return filtered[:limit]

    def delete_all(self, *, user_id: str) -> None:
        self._store[user_id] = []

    def get_all(self, *, user_id: str, limit: int = 1) -> list[dict[str, Any]]:
        return self._store.get(user_id, [])[:limit]


@pytest.fixture
def adapter() -> Mem0Adapter:
    fake_client = _FakeMem0Client()
    sidecar = SidecarStore(backend="memory")
    embedding = _FakeEmbeddingService()

    class _TestMem0Adapter(Mem0Adapter):
        @staticmethod
        def _create_client(config: dict[str, Any]) -> Any:
            return fake_client

    return _TestMem0Adapter(
        config={
            "llm": {"model": "qwen3-max"},
            "embedding": {"model": "BAAI/bge-m3"},
        },
        sidecar=sidecar,
        embedding_service=embedding,
        disable_telemetry=True,
    )


def _mk_turn(turn_id: str, text: str, ts: float) -> Turn:
    return Turn(
        turn_id=turn_id,
        session_id="s1",
        role="user",
        text=text,
        timestamp=ts,
    )


def test_write_injects_metadata_and_sidecar(adapter: Mem0Adapter) -> None:
    turn = _mk_turn("t_001", "团子喜欢吃冻干", 1000.0)
    stats = adapter.write("u1", turn)

    assert stats.success
    rows = adapter.client.get_all(user_id="u1", limit=10)
    assert rows[0]["metadata"]["turn_id"] == "t_001"
    assert rows[0]["metadata"]["session_id"] == "s1"

    memory_id = rows[0]["id"]
    assert adapter._sidecar.get_memory_ids("u1", "t_001") == [memory_id]


def test_read_wraps_memories_with_sidecar_fallback(adapter: Mem0Adapter) -> None:
    adapter.write("u1", _mk_turn("t_001", "团子是布偶猫", 1000.0))
    adapter.write("u1", _mk_turn("t_002", "团子喜欢晒太阳", 1001.0))

    rows = adapter.client.get_all(user_id="u1", limit=10)
    rows[1]["metadata"] = {}

    result = adapter.read("u1", "团子", ReadOpts(top_k=5, include_provenance=True))

    assert len(result.memories) >= 1
    first = result.memories[0]
    assert first.memory_id
    assert first.text
    assert 0.0 <= first.score <= 1.0
    assert isinstance(first.source_turn_ids, list)

    fallback = next(m for m in result.memories if m.memory_id == rows[1]["id"])
    assert fallback.source_turn_ids == ["t_002"]
    assert fallback.source_embedding is not None


def test_read_skip_provenance_is_faster_path(adapter: Mem0Adapter) -> None:
    embedding = adapter._embedding_service
    adapter.write("u1", _mk_turn("t_001", "团子喜欢猫抓板", 1000.0))
    result = adapter.read("u1", "团子", ReadOpts(top_k=5, include_provenance=False))

    assert len(result.memories) == 1
    assert result.memories[0].source_turn_ids == []
    assert result.memories[0].source_embedding is None
    assert embedding.calls == 0


def test_clear_syncs_mem0_and_sidecar(adapter: Mem0Adapter) -> None:
    adapter.write("u1", _mk_turn("t_001", "团子会握手", 1000.0))
    adapter.flush("u1")

    clear_stats = adapter.clear("u1")
    after = adapter.read("u1", "团子", ReadOpts(top_k=5))

    assert clear_stats.success
    assert clear_stats.deleted_count >= 1
    assert after.memories == []
    assert adapter._sidecar.stats("u1")["mapping_count"] == 0


def test_capability_profile_complete_and_lane(adapter: Mem0Adapter) -> None:
    profile = adapter.get_capability_profile()

    assert profile.adapter_name == "mem0"
    assert len(profile.capabilities) == 11
    features = {cap.feature for cap in profile.capabilities}
    assert len(features) == 11

    assert profile.lane_compatibility["A1"] == "compatible"
    assert profile.lane_compatibility["A2"] == "compatible"
    assert profile.lane_compatibility["A3"] == "degraded"
    assert profile.lane_compatibility["B"] == "compatible"
    assert profile.lane_compatibility["C"] == "incompatible"


def test_batch_write_native_single_call(adapter: Mem0Adapter) -> None:
    turns = [
        _mk_turn("t_001", "团子第一条", 1000.0),
        _mk_turn("t_002", "团子第二条", 1001.0),
    ]
    results = adapter.batch_write("u1", turns)

    assert len(results) == 2
    assert all(r.success for r in results)
    memories_t1 = adapter._sidecar.get_memory_ids("u1", "t_001")
    memories_t2 = adapter._sidecar.get_memory_ids("u1", "t_002")
    assert memories_t1 and memories_t2


@pytest.mark.integration
def test_mem0_integration_write_search_clear() -> None:
    has_mem0 = importlib.util.find_spec("mem0") is not None
    has_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY"))
    if not has_mem0 or not has_key:
        pytest.skip("requires mem0 package and OPENAI_API_KEY/DASHSCOPE_API_KEY")

    adapter = Mem0Adapter(disable_telemetry=True)
    user_id = f"u_int_{int(time.time())}"

    turns = [
        _mk_turn("t_001", "我们家有只布偶猫叫团子", time.time()),
        _mk_turn("t_002", "团子最喜欢吃冻干", time.time() + 1),
        _mk_turn("t_003", "团子喜欢在窗边晒太阳", time.time() + 2),
    ]

    for turn in turns:
        adapter.write(user_id, turn)
    adapter.flush(user_id)

    read = adapter.read(user_id, "团子喜欢吃什么", ReadOpts(top_k=5))
    assert isinstance(read.memories, list)

    adapter.clear(user_id)
    read_after_clear = adapter.read(user_id, "团子", ReadOpts(top_k=5))
    assert read_after_clear.memories == []
