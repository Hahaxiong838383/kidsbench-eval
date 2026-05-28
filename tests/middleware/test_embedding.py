from __future__ import annotations

import importlib

import pytest

from kidsbench.contract import AdapterError
from kidsbench.middleware.embedding import (
    BgeM3Local,
    CachedEmbedding,
    EmbeddingService,
    GeminiEmbedding,
)


class DummyEmbedding(EmbeddingService):
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[float(len(t)), 1.0] for t in texts]

    def dim(self) -> int:
        return 2


def test_bge_m3_local_missing_dependency(monkeypatch) -> None:
    def _fake_import(name: str):
        if name == "FlagEmbedding":
            raise ImportError("missing")
        return importlib.import_module(name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    with pytest.raises(AdapterError, match="FlagEmbedding"):
        BgeM3Local()


def test_cached_embedding_hits_memory_cache() -> None:
    inner = DummyEmbedding()
    cached = CachedEmbedding(inner=inner, max_size=10)

    first = cached.embed(["abc", "def"])
    second = cached.embed(["abc"])
    assert first[0] == second[0]
    assert inner.calls == 1


def test_cached_embedding_sqlite_persistent(tmp_path) -> None:
    path = tmp_path / "cache.sqlite3"
    inner1 = DummyEmbedding()
    cached1 = CachedEmbedding(inner=inner1, cache_path=path)
    out1 = cached1.embed(["hello"])
    assert out1[0][0] == 5.0

    class NoCallEmbedding(EmbeddingService):
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("should not call inner embed")

        def dim(self) -> int:
            return 2

    cached2 = CachedEmbedding(inner=NoCallEmbedding(), cache_path=path)
    out2 = cached2.embed(["hello"])
    assert out2[0][0] == 5.0


def test_gemini_embedding_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PROXY_KEY", raising=False)
    service = GeminiEmbedding()
    with pytest.raises(AdapterError, match="missing api key"):
        service.embed(["x"])


def test_gemini_embedding_success(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PROXY_KEY", "k")

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"data": [{"embedding": [0.1, 0.2]}]}

        text = ""

    class FakeClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, json: dict, headers: dict):
            assert url.endswith("/v1/embeddings")
            assert json["input"] == ["x"]
            assert headers["Authorization"] == "Bearer k"
            return FakeResponse()

    import kidsbench.middleware.embedding as emb_mod

    monkeypatch.setattr(emb_mod.httpx, "Client", FakeClient)
    service = GeminiEmbedding()
    vectors = service.embed(["x"])
    assert vectors == [[0.1, 0.2]]
    assert service.dim() == 2


def test_cached_embedding_empty_input() -> None:
    service = CachedEmbedding(inner=DummyEmbedding())
    assert service.embed([]) == []
