from __future__ import annotations

import importlib.util
import os
import time
from typing import Any

import pytest

from kidsbench.adapters.mem0_adapter import Mem0Adapter
from kidsbench.contract import STANDARD_FEATURES, ReadOpts, Turn
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
    assert len(profile.capabilities) == len(STANDARD_FEATURES)
    features = {cap.feature for cap in profile.capabilities}
    assert len(features) == len(STANDARD_FEATURES)

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
    """真实 mem0 SDK 集成测试。

    必跑环境: .venv-mem0 (含 mem0ai + sentence-transformers)
    LLM: GEMINI_PROXY (OpenAI 兼容)
    Embedder: 本地 sentence-transformers/all-MiniLM-L6-v2 (384 dim)
    Vector store: 本地 qdrant path 模式
    """
    has_mem0 = importlib.util.find_spec("mem0") is not None
    has_st = importlib.util.find_spec("sentence_transformers") is not None
    if not (has_mem0 and has_st):
        pytest.skip("requires mem0 + sentence-transformers (use .venv-mem0)")

    os.environ.setdefault("MEM0_TELEMETRY", "false")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "kidsbench_test",
                "embedding_model_dims": 384,
                "path": f"/tmp/kidsbench_qdrant_test_{int(time.time())}",
                "on_disk": False,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gemini-3.5-flash",
                "api_key": "fq8-1NLtsbVsiJhZaISmNeobvqY0bIZMoafPnKfkuz4",
                "openai_base_url": "http://23.226.135.149:4000/v1",
                "temperature": 0.0,
            },
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_dims": 384,
            },
        },
    }

    adapter = Mem0Adapter(config=config, disable_telemetry=True)
    user_id = f"u_int_{int(time.time())}"

    turns = [
        _mk_turn("t_001", "我们家有只布偶猫叫团子", time.time()),
        _mk_turn("t_002", "团子最喜欢吃冻干", time.time() + 1),
        _mk_turn("t_003", "团子喜欢在窗边晒太阳", time.time() + 2),
    ]

    for turn in turns:
        adapter.write(user_id, turn)
    adapter.flush(user_id)

    # 验证 read 召回 + source_turn_ids 至少有一个非空（sidecar 兜底应该有）
    read = adapter.read(user_id, "团子喜欢吃什么", ReadOpts(top_k=5))
    assert isinstance(read.memories, list)
    # 至少召回 1 条（mem0 LLM 可能合并掉一些）
    assert len(read.memories) >= 1, "mem0 没召回任何记忆 — 集成失败"
    # 至少一条 memory 有 source_turn_ids（sidecar 兜底）
    assert any(m.source_turn_ids for m in read.memories), \
        "所有 memory 都缺 source_turn_ids — 双兜底失效"

    adapter.clear(user_id)
    read_after_clear = adapter.read(user_id, "团子", ReadOpts(top_k=5))
    assert read_after_clear.memories == [], "clear 后还有幽灵记忆"
