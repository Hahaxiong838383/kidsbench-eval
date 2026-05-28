"""Embedding services with optional persistent cache."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx

from kidsbench.contract import AdapterError


class EmbeddingService(ABC):
    """Embedding service contract."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""

    @abstractmethod
    def dim(self) -> int:
        """Return embedding vector dimension."""


class BgeM3Local(EmbeddingService):
    """Local embedding service backed by FlagEmbedding."""

    def __init__(self, model_path: str | None = None, batch_size: int = 32) -> None:
        try:
            module = importlib.import_module("FlagEmbedding")
        except Exception as err:
            raise AdapterError(
                "FlagEmbedding is required for BgeM3Local, please install optional dependency"
            ) from err

        model_cls = getattr(module, "BGEM3FlagModel", None)
        if model_cls is None:
            model_cls = getattr(module, "FlagModel", None)
        if model_cls is None:
            raise AdapterError("FlagEmbedding missing BGEM3FlagModel/FlagModel")

        model_name = model_path or "BAAI/bge-m3"
        self._model: Any = model_cls(model_name)
        self._batch_size = batch_size
        self._dim_cache: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with local BGE model."""
        if not texts:
            return []
        encoded = self._model.encode(texts, batch_size=self._batch_size)
        dense_vecs = encoded.get("dense_vecs") if isinstance(encoded, dict) else encoded
        vectors = [list(map(float, vec)) for vec in dense_vecs]
        if vectors:
            self._dim_cache = len(vectors[0])
        return vectors

    def dim(self) -> int:
        """Return cached embedding dimension if available."""
        if self._dim_cache is None:
            vectors = self.embed(["dim probe"])
            self._dim_cache = len(vectors[0]) if vectors else 0
        return self._dim_cache


class GeminiEmbedding(EmbeddingService):
    """Embedding service through Gemini proxy endpoint."""

    def __init__(
        self,
        endpoint: str = "http://23.226.135.149:4000",
        api_key_env: str = "GEMINI_PROXY_KEY",
        model: str = "text-embedding-004",
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key_env = api_key_env
        self._model = model
        self._dim_cache = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts through HTTP API."""
        if not texts:
            return []
        api_key = os.getenv(self._api_key_env)
        if not api_key:
            raise AdapterError(f"missing api key env: {self._api_key_env}")

        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": self._model, "input": texts}
        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{self._endpoint}/v1/embeddings", json=payload, headers=headers)
        if response.status_code >= 400:
            raise AdapterError(f"embedding request failed: {response.status_code} {response.text}")

        data = response.json().get("data", [])
        vectors = [list(map(float, row["embedding"])) for row in data]
        if vectors:
            self._dim_cache = len(vectors[0])
        return vectors

    def dim(self) -> int:
        """Return configured/observed embedding dimension."""
        return self._dim_cache


class CachedEmbedding(EmbeddingService):
    """LRU cache with optional SQLite persistence for embeddings."""

    def __init__(
        self,
        inner: EmbeddingService,
        max_size: int = 100000,
        cache_path: Path | None = None,
    ) -> None:
        self._inner = inner
        self._max_size = max_size
        self._mem_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(cache_path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache(text_hash TEXT PRIMARY KEY, vec BLOB NOT NULL)"
            )
            self._conn.commit()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings while reusing memory/sqlite caches."""
        if not texts:
            return []

        hashes = [_hash_text(t) for t in texts]
        result_map: dict[str, list[float]] = {}
        with self._lock:
            for key in hashes:
                vec = self._mem_cache.get(key)
                if vec is not None:
                    self._mem_cache.move_to_end(key)
                    result_map[key] = vec

            missing = [k for k in dict.fromkeys(hashes) if k not in result_map]
            if missing and self._conn is not None:
                result_map.update(self._fetch_sqlite(missing))
                for key in missing:
                    if key in result_map:
                        self._store_mem(key, result_map[key])
            missing = [k for k in dict.fromkeys(hashes) if k not in result_map]

        if missing:
            missing_texts = [texts[hashes.index(key)] for key in missing]
            vectors = self._inner.embed(missing_texts)
            with self._lock:
                for key, vec in zip(missing, vectors, strict=True):
                    result_map[key] = vec
                    self._store_mem(key, vec)
                    self._store_sqlite(key, vec)

        return [result_map[key] for key in hashes]

    def dim(self) -> int:
        """Delegate dimension lookup to underlying service."""
        return self._inner.dim()

    def _fetch_sqlite(self, keys: list[str]) -> dict[str, list[float]]:
        if self._conn is None or not keys:
            return {}

        out: dict[str, list[float]] = {}
        chunk_size = 500
        for idx in range(0, len(keys), chunk_size):
            chunk = keys[idx : idx + chunk_size]
            placeholders = ", ".join("?" for _ in chunk)
            sql = f"SELECT text_hash, vec FROM cache WHERE text_hash IN ({placeholders})"
            rows = self._conn.execute(sql, chunk).fetchall()
            for key, blob in rows:
                out[str(key)] = json.loads(blob.decode("utf-8"))
        return out

    def _store_mem(self, key: str, vec: list[float]) -> None:
        self._mem_cache[key] = vec
        self._mem_cache.move_to_end(key)
        while len(self._mem_cache) > self._max_size:
            self._mem_cache.popitem(last=False)

    def _store_sqlite(self, key: str, vec: list[float]) -> None:
        if self._conn is None:
            return
        blob = json.dumps(vec, separators=(",", ":")).encode("utf-8")
        self._conn.execute(
            "INSERT OR REPLACE INTO cache(text_hash, vec) VALUES (?, ?)",
            (key, blob),
        )
        self._conn.commit()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
