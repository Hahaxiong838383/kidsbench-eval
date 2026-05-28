"""NoMemoryAdapter：地板基线。

不接任何记忆系统。write 接收但不存，read 永远返回空。
用来量化「不给 LLM 记忆时它能蒙对多少题」——参数化知识基线。
"""
from __future__ import annotations

import time
from typing import Any

from ..contract import (
    STANDARD_FEATURES,
    AdapterError,
    Capability,
    CapabilityProfile,
    ClearStats,
    Dependency,
    FlushStats,
    MemoryAdapter,
    ReadOpts,
    ReadResult,
    Turn,
    WriteStats,
)


class NoMemoryAdapter(MemoryAdapter):
    """不存储任何记忆，read 永远空。地板基线。"""

    name = "nomemory"
    paradigm_tags = {
        "representation": "none",
        "retrieval": "none",
        "write_policy": "none",
        "controller": "none",
        "cognitive": [],
    }

    def __init__(self) -> None:
        self._write_count: dict[str, int] = {}
        self._read_count: dict[str, int] = {}

    def write(self, user_id: str, turn: Turn) -> WriteStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.time()
        self._write_count[user_id] = self._write_count.get(user_id, 0) + 1
        return WriteStats(success=True, latency_ms=(time.time() - t0) * 1000)

    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.time()
        self._read_count[user_id] = self._read_count.get(user_id, 0) + 1
        return ReadResult(memories=[], latency_ms=(time.time() - t0) * 1000)

    def clear(self, user_id: str) -> ClearStats:
        t0 = time.time()
        deleted = self._write_count.pop(user_id, 0)
        self._read_count.pop(user_id, None)
        return ClearStats(success=True, latency_ms=(time.time() - t0) * 1000, deleted_count=deleted)

    def flush(self, user_id: str) -> FlushStats:
        return FlushStats(success=True, latency_ms=0.0)

    def get_dependencies(self) -> list[Dependency]:
        return []  # 无任何外部依赖

    def get_stats(self, user_id: str) -> dict[str, Any]:
        return {
            "total_writes": self._write_count.get(user_id, 0),
            "total_reads": self._read_count.get(user_id, 0),
        }

    def get_capability_profile(self) -> CapabilityProfile:
        # NoMemory 不做任何事，所有能力一律 unsupported（诚实声明）
        caps = [
            Capability(feature=f, level="unsupported", note="NoMemory baseline")
            for f in STANDARD_FEATURES
        ]
        # 无任何内部 LLM/embed，所有 Lane 都兼容（含 A3 本地 LLM、C 无 LLM 档）
        return CapabilityProfile(
            adapter_name=self.name,
            capabilities=caps,
            lane_compatibility={
                "A1": "compatible",
                "A2": "compatible",
                "A3": "compatible",
                "B": "compatible",
                "C": "compatible",
            },
            lane_notes={},
        )
