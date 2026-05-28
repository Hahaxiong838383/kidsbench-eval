"""OracleAdapter：天花板基线。

read 时根据题库标注的 gold_memory_ids 完美召回对应 turn 文本。
用来量化「假设召回 100% 准确，LLM 能答对多少题」——召回上限基线。

注意：OracleAdapter 需要外部注入 gold_lookup 函数，由 Harness 在每题前 set_gold()。
"""
from __future__ import annotations

import time
from typing import Any, Callable

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

# Harness 提供的 gold 查询回调：(user_id, query) -> list[turn_id]
GoldLookup = Callable[[str, str], list[str]]


class OracleAdapter(MemoryAdapter):
    """根据 gold_memory_ids 完美召回。天花板基线。"""

    name = "oracle"
    paradigm_tags = {
        "representation": "raw_text",
        "retrieval": "gold_lookup",
        "write_policy": "append_only",
        "controller": "oracle",
        "cognitive": ["episodic", "semantic", "procedural"],
    }

    def __init__(self, gold_lookup: GoldLookup | None = None) -> None:
        self._store: dict[str, dict[str, Turn]] = {}  # {user_id: {turn_id: Turn}}
        self._gold_lookup: GoldLookup | None = gold_lookup

    def set_gold_lookup(self, gold_lookup: GoldLookup) -> None:
        """Harness 每题前调一次，注入该题的 gold_memory_ids 查询逻辑。"""
        self._gold_lookup = gold_lookup

    def write(self, user_id: str, turn: Turn) -> WriteStats:
        if not user_id:
            raise AdapterError("user_id must not be empty")
        t0 = time.time()
        self._store.setdefault(user_id, {})[turn.turn_id] = turn
        return WriteStats(success=True, latency_ms=(time.time() - t0) * 1000)

    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:  # noqa: ARG002
        if not user_id:
            raise AdapterError("user_id must not be empty")
        if self._gold_lookup is None:
            raise AdapterError(
                "OracleAdapter requires gold_lookup; Harness must call set_gold_lookup() before each question"
            )
        t0 = time.time()
        gold_ids = self._gold_lookup(user_id, query)
        store = self._store.get(user_id, {})
        memories: list[Memory] = []
        for tid in gold_ids:
            if tid not in store:
                # gold 标注引用了不存在的 turn，是题库 bug，必须暴露
                raise AdapterError(f"gold turn_id '{tid}' not found in store for user '{user_id}'")
            t = store[tid]
            memories.append(
                Memory(
                    memory_id=f"oracle_{t.turn_id}",
                    text=f"[{t.role}] {t.text}",
                    score=1.0,
                    source_turn_ids=[t.turn_id],
                    timestamp=t.timestamp,
                )
            )
        return ReadResult(memories=memories, latency_ms=(time.time() - t0) * 1000)

    def clear(self, user_id: str) -> ClearStats:
        t0 = time.time()
        deleted = len(self._store.pop(user_id, {}))
        return ClearStats(success=True, latency_ms=(time.time() - t0) * 1000, deleted_count=deleted)

    def flush(self, user_id: str) -> FlushStats:  # noqa: ARG002
        return FlushStats(success=True, latency_ms=0.0)

    def get_dependencies(self) -> list[Dependency]:
        return []

    def get_stats(self, user_id: str) -> dict[str, Any]:
        return {
            "total_writes": len(self._store.get(user_id, {})),
            "has_gold_lookup": self._gold_lookup is not None,
        }

    def get_capability_profile(self) -> CapabilityProfile:
        caps_map = {
            "physical_clear": ("native", "in-memory dict pop"),
            "turn_id_traceback": ("native", "直接从 gold 取 id"),
            "cognitive_type_filter": ("computed", "靠 Turn.metadata.cognitive_type 过滤"),
            "score_normalized": ("declared", "全部 1.0（gold 即满分）"),
            "concurrent_safe": ("declared", "dict 非并发安全，单进程跑评测可"),
            "cost_accounting": ("unsupported", "无 LLM 调用"),
            "embedding_export": ("unsupported", "本基线不做 embedding"),
            "flush_blocking": ("native", "同步内存"),
        }
        caps = [
            Capability(feature=f, level=lvl, note=note)  # type: ignore[arg-type]
            for f in STANDARD_FEATURES
            for lvl, note in [caps_map[f]]
        ]
        # Oracle 不调任何 LLM/embed，所有 Lane 兼容
        return CapabilityProfile(
            adapter_name=self.name,
            capabilities=caps,
            lane_compatibility={"A1": "compatible", "A2": "compatible", "B": "compatible"},
            lane_notes={},
        )
