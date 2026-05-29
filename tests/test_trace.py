"""trace 模块单测（B1.0）。

覆盖：
- is_tracing 范围
- init_run 嵌套 + ENTER/EXIT 配对
- @span sync / async
- span_attr 动态属性
- 异常路径 EXIT 带 error
- JsonlExporter 写文件
- MultiExporter 多通道
- NullExporter 静默
- exporter 失败不抛错到业务
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from kidsbench.trace import (
    Exporter,
    JsonlExporter,
    MultiExporter,
    NullExporter,
    SpanEvent,
    finalize_run,
    init_run,
    is_tracing,
    set_exporter,
    span,
    span_attr,
)


class CollectingExporter(Exporter):
    """收集所有 events 到 list，方便断言。"""

    def __init__(self) -> None:
        self.events: list[SpanEvent] = []

    def export(self, event: SpanEvent) -> None:
        self.events.append(event)


@pytest.fixture
def collector():
    c = CollectingExporter()
    set_exporter(c)
    yield c
    set_exporter(None)


# ============================================================
# 基础
# ============================================================

def test_is_tracing_default_false():
    set_exporter(None)
    assert is_tracing() is False


def test_is_tracing_within_init_run(collector):
    with init_run("r_test_1", qid="q_001"):
        assert is_tracing() is True
    assert is_tracing() is False


def test_init_run_emits_enter_exit(collector):
    with init_run("r_test_2"):
        pass
    types = [e.type for e in collector.events]
    assert "ENTER" in types and "EXIT" in types
    assert collector.events[0].name == "run_root"
    assert collector.events[-1].name == "run_root"
    assert collector.events[-1].attrs["success"] is True


def test_init_run_propagates_attrs(collector):
    with init_run("r_test_3", qid="q_001", adapter="mem0"):
        pass
    enter = collector.events[0]
    assert enter.attrs["qid"] == "q_001"
    assert enter.attrs["adapter"] == "mem0"


def test_init_run_event_ids_increment(collector):
    with init_run("r_test_4"):
        pass
    ids = [e.event_id for e in collector.events]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


# ============================================================
# @span 装饰器
# ============================================================

def test_span_decorator_sync(collector):
    @span("test.sync_func")
    def inner(x: int) -> int:
        return x * 2

    with init_run("r_t_sync"):
        result = inner(5)

    assert result == 10
    span_events = [e for e in collector.events if e.name == "test.sync_func"]
    assert len(span_events) == 2  # ENTER + EXIT
    assert span_events[0].type == "ENTER"
    assert span_events[1].type == "EXIT"
    assert span_events[1].duration_ms is not None and span_events[1].duration_ms >= 0


def test_span_decorator_async(collector):
    @span("test.async_func")
    async def inner(x: int) -> int:
        await asyncio.sleep(0.001)
        return x + 1

    with init_run("r_t_async"):
        result = asyncio.run(inner(7))

    assert result == 8
    span_events = [e for e in collector.events if e.name == "test.async_func"]
    assert len(span_events) == 2
    assert span_events[1].duration_ms >= 1  # sleep 1ms


def test_span_nested_parent_id(collector):
    @span("outer")
    def outer():
        inner()

    @span("inner")
    def inner():
        pass

    with init_run("r_t_nest"):
        outer()

    outer_enter = next(e for e in collector.events if e.name == "outer" and e.type == "ENTER")
    inner_enter = next(e for e in collector.events if e.name == "inner" and e.type == "ENTER")
    assert inner_enter.parent_id == outer_enter.span_id


def test_span_exception_emits_exit_with_error(collector):
    @span("test.raises")
    def raises():
        raise ValueError("boom")

    with init_run("r_t_err"):
        with pytest.raises(ValueError, match="boom"):
            raises()

    exit_events = [e for e in collector.events if e.name == "test.raises" and e.type == "EXIT"]
    assert len(exit_events) == 1
    assert "error" in exit_events[0].attrs
    assert "boom" in exit_events[0].attrs["error"]


def test_span_no_trace_is_noop():
    """无 init_run 时，装饰器零开销直通"""
    set_exporter(None)

    @span("never.traced")
    def func(x):
        return x

    # 不在 init_run 里调用，直接返回，不报错
    assert func(42) == 42


# ============================================================
# span_attr 动态属性
# ============================================================

def test_span_attr_emits_attr_event(collector):
    @span("with_attrs")
    def func():
        span_attr(prompt_tokens=100, completion_tokens=50)

    with init_run("r_t_attr"):
        func()

    attr_events = [e for e in collector.events if e.type == "ATTR"]
    assert len(attr_events) == 1
    assert attr_events[0].attrs["prompt_tokens"] == 100
    assert attr_events[0].attrs["completion_tokens"] == 50


def test_span_attr_without_trace_silent():
    set_exporter(None)
    # 不报错就 OK
    span_attr(foo="bar")


# ============================================================
# Exporter
# ============================================================

def test_jsonl_exporter_writes_file(tmp_path):
    path = tmp_path / "pipeline.jsonl"
    exporter = JsonlExporter(path)
    set_exporter(exporter)
    try:
        with init_run("r_jsonl"):
            @span("test_jsonl_inner")
            def f():
                pass
            f()

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        events = [json.loads(line) for line in lines]
        names = [e["name"] for e in events]
        assert "run_root" in names
        assert "test_jsonl_inner" in names
        # 必含 event_id / span_id / type / ts
        for e in events:
            assert "event_id" in e
            assert "span_id" in e
            assert "type" in e
            assert "ts" in e
    finally:
        set_exporter(None)


def test_multi_exporter_dispatches(tmp_path):
    c1 = CollectingExporter()
    c2 = CollectingExporter()
    multi = MultiExporter([c1, c2])
    set_exporter(multi)
    try:
        with init_run("r_multi"):
            pass
        assert len(c1.events) == 2  # ENTER + EXIT
        assert len(c2.events) == 2
    finally:
        set_exporter(None)


def test_failing_exporter_does_not_break_business():
    """exporter 抛错时 init_run + @span 仍能正常返回。"""

    class BrokenExporter(Exporter):
        def export(self, event):
            raise RuntimeError("exporter exploded")

    set_exporter(BrokenExporter())
    try:
        @span("test.broken_exporter")
        def func(x):
            return x + 1

        # init_run 内 + 函数内 + finalize，三处都不抛业务异常
        with init_run("r_broken"):
            assert func(5) == 6
    finally:
        set_exporter(None)


def test_null_exporter_no_events():
    e = NullExporter()
    set_exporter(e)
    try:
        with init_run("r_null"):
            pass
    finally:
        set_exporter(None)
    # null exporter 不抛错就 OK


# ============================================================
# 嵌套 init_run 不重叠
# ============================================================

def test_event_counter_isolated_per_run(collector):
    with init_run("r_first"):
        pass
    first_ids = [e.event_id for e in collector.events]
    collector.events.clear()
    with init_run("r_second"):
        pass
    second_ids = [e.event_id for e in collector.events]
    # 每个 run 独立计数（contextvars reset）
    assert first_ids[0] == second_ids[0] == 1
