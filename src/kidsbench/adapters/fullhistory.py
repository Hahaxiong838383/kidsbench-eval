"""FullHistoryAdapter：对照基线。

不做任何召回，read 时把该 user_id 所有 turn 文本按时间序拼接全部返回。
用来回答「长文本时代直接塞所有历史能不能击败候选系统」——这是最难打的基线。
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
    Memory,
    MemoryAdapter,
    ReadOpts,
    ReadResult,
    Turn,
    WriteStats,
)


class FullHistoryAdapter(MemoryAdapter):
    """全量历史拼接，无召回逻辑。对照基线。"""

    name = "fullhistory"
    paradigm_tags = {
        "representation": "raw_text",
        "retrieval": "none",
        "write_policy": "append_only",
        "controller": "none",
        "cognitive": ["episodic"],  # 原始事件流即 episodic
    }

    def __init__(self, max_chars: int = 50000) -> None:
        """max_chars: 单次 read 返回的拼接文本上限，防 prompt 爆炸。"""
        self.max_chars = max_chars
        # 内存存储：{user_id: [Turn, ...]}
        self._store: dict[str, list[Turn]] = {}

    def write(self, user_id: str, turn: Turn) -> WriteStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.time()
        self._store.setdefault(user_id, []).append(turn)
        return WriteStats(success=True, latency_ms=(time.time() - t0) * 1000)

    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:  # noqa: ARG002
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.time()
        turns = sorted(self._store.get(user_id, []), key=lambda t: t.timestamp)
        memories: list[Memory] = []
        total_chars = 0
        for turn in turns:
            if total_chars + len(turn.text) > self.max_chars:
                break
            memories.append(
                Memory(
                    memory_id=f"fh_{turn.turn_id}",
                    text=f"[{turn.role}] {turn.text}",
                    score=1.0,  # 全部认为相关，对照基线不做相关性
                    source_turn_ids=[turn.turn_id],
                    timestamp=turn.timestamp,
                )
            )
            total_chars += len(turn.text)
        return ReadResult(memories=memories, latency_ms=(time.time() - t0) * 1000)

    def clear(self, user_id: str) -> ClearStats:
        t0 = time.time()
        deleted = len(self._store.pop(user_id, []))
        return ClearStats(success=True, latency_ms=(time.time() - t0) * 1000, deleted_count=deleted)

    def flush(self, user_id: str) -> FlushStats:  # noqa: ARG002
        return FlushStats(success=True, latency_ms=0.0)  # 同步内存，无需 flush

    def get_dependencies(self) -> list[Dependency]:
        return []

    def get_stats(self, user_id: str) -> dict[str, Any]:
        turns = self._store.get(user_id, [])
        return {
            "total_writes": len(turns),
            "total_chars": sum(len(t.text) for t in turns),
        }

    def get_capability_profile(self) -> CapabilityProfile:
        caps_map = {
            "physical_clear": ("native", "in-memory dict pop"),
            "turn_id_traceback": ("native", "Turn 原样保存"),
            "cognitive_type_filter": ("unsupported", "不做分类"),
            "score_normalized": ("declared", "全部 1.0，无相关性排序"),
            "concurrent_safe": ("declared", "dict 非并发安全，单进程跑评测可"),
            "cost_accounting": ("unsupported", "无 LLM 调用"),
            "embedding_export": ("unsupported", "无 embedding"),
            "flush_blocking": ("native", "同步写内存，无需 flush"),
        }
        caps = [
            Capability(feature=f, level=lvl, note=note)  # type: ignore[arg-type]
            for f in STANDARD_FEATURES
            for lvl, note in [caps_map[f]]
        ]
        # 全量历史拼接 = 纯 raw text，无内部 LLM/embed，所有 Lane 兼容
        return CapabilityProfile(
            adapter_name=self.name,
            capabilities=caps,
            lane_compatibility={"A1": "compatible", "A2": "compatible", "B": "compatible"},
            lane_notes={},
        )
