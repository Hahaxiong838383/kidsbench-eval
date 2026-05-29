"""Span 装饰器 + contextvars 上下文传递。

设计：
- run_id / current_span_id 走 contextvars，跨 async/sync/线程边界自动跟随
- @span("name") 同时支持 sync 和 async（通过 inspect.iscoroutinefunction）
- ENTER/EXIT 成对发出；异常路径补 EXIT 带 error 字段
- 不 init_run 时所有 span 静默 no-op（is_tracing() 返回 False）

按 CLAUDE.md 原则：
- 不可变：SpanEvent 用 dataclass(frozen=True)
- 错误处理：exporter 失败不阻断业务，统一 try/except
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, TypeVar


# ============================================================
# 数据模型（不可变）
# ============================================================

@dataclass(frozen=True)
class SpanEvent:
    """单条 span 事件（ENTER 或 EXIT）。"""

    event_id: int
    span_id: str
    parent_id: str | None
    name: str
    type: str  # "ENTER" | "EXIT"
    ts: float
    duration_ms: float | None = None  # 仅 EXIT
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "event_id": self.event_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "type": self.type,
            "ts": self.ts,
        }
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        d.update(self.attrs)
        return d


# ============================================================
# 上下文变量
# ============================================================

_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kidsbench_run_id", default=None
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kidsbench_span_id", default=None
)
_event_counter: contextvars.ContextVar[list[int]] = contextvars.ContextVar(
    "kidsbench_event_counter", default=[0]
)


def is_tracing() -> bool:
    """当前是否在 trace 范围内（init_run 内）。"""
    return _current_run_id.get() is not None


# ============================================================
# Exporter 注入（避免循环导入：exporter 模块自己注册）
# ============================================================

_active_exporter: Any = None  # 由 exporter.set_exporter() 设置


def _emit(event: SpanEvent) -> None:
    """送一条 event 给 exporter。失败静默吞掉。"""
    if _active_exporter is None:
        return
    try:
        _active_exporter.export(event)
    except Exception:  # noqa: BLE001 - exporter 失败不阻断业务
        pass


def _set_exporter_internal(exporter: Any) -> None:
    """exporter 模块调，避免循环 import。"""
    global _active_exporter
    _active_exporter = exporter


def _next_event_id() -> int:
    counter = _event_counter.get()
    counter[0] += 1
    return counter[0]


# ============================================================
# init_run（harness 入口）
# ============================================================

@contextmanager
def init_run(run_id: str, **root_attrs: Any) -> Iterator[None]:
    """初始化一个 trace run 上下文。

    用法：
        with init_run("r_001", qid="q_001", adapter="mem0"):
            harness.run_question(...)

    退出 with 块时自动 finalize（发 root EXIT + 关 exporter）。
    """
    root_span_id = f"sp-root-{uuid.uuid4().hex[:8]}"
    tok_run = _current_run_id.set(run_id)
    tok_span = _current_span_id.set(root_span_id)
    tok_counter = _event_counter.set([0])

    started_at = time.monotonic()
    _emit(SpanEvent(
        event_id=_next_event_id(),
        span_id=root_span_id,
        parent_id=None,
        name="run_root",
        type="ENTER",
        ts=time.time(),
        attrs={"run_id": run_id, **root_attrs},
    ))
    try:
        yield
        duration = (time.monotonic() - started_at) * 1000
        _emit(SpanEvent(
            event_id=_next_event_id(),
            span_id=root_span_id,
            parent_id=None,
            name="run_root",
            type="EXIT",
            ts=time.time(),
            duration_ms=duration,
            attrs={"success": True},
        ))
    except Exception as exc:
        duration = (time.monotonic() - started_at) * 1000
        _emit(SpanEvent(
            event_id=_next_event_id(),
            span_id=root_span_id,
            parent_id=None,
            name="run_root",
            type="EXIT",
            ts=time.time(),
            duration_ms=duration,
            attrs={"success": False, "error": repr(exc)[:300]},
        ))
        raise
    finally:
        _current_run_id.reset(tok_run)
        _current_span_id.reset(tok_span)
        _event_counter.reset(tok_counter)
        finalize_run()


def finalize_run() -> None:
    """刷 exporter 缓冲，关连接。供 harness 在 with 结束后手动调（with 自带）。"""
    if _active_exporter is None:
        return
    try:
        flush = getattr(_active_exporter, "flush", None)
        if callable(flush):
            flush()
    except Exception:  # noqa: BLE001
        pass


# ============================================================
# @span 装饰器
# ============================================================

F = TypeVar("F", bound=Callable)


def span(name: str, **static_attrs: Any) -> Callable[[F], F]:
    """函数装饰器：包成一个 span。

    用法：
        @span("embedding.encode")
        def encode(self, text):
            ...

    动态属性：函数内可调 span_attr(key=value) 给当前 span 加属性。
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):  # noqa: ANN
                return await _run_span_async(name, static_attrs, func, args, kwargs)
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):  # noqa: ANN
            return _run_span_sync(name, static_attrs, func, args, kwargs)
        return sync_wrapper  # type: ignore[return-value]

    return decorator


def _run_span_sync(name: str, static_attrs: dict, func: Callable, args, kwargs):
    if not is_tracing():
        return func(*args, **kwargs)

    parent_id = _current_span_id.get()
    span_id = f"sp-{uuid.uuid4().hex[:10]}"
    tok = _current_span_id.set(span_id)
    started_at = time.monotonic()

    _emit(SpanEvent(
        event_id=_next_event_id(),
        span_id=span_id,
        parent_id=parent_id,
        name=name,
        type="ENTER",
        ts=time.time(),
        attrs=dict(static_attrs),
    ))
    try:
        result = func(*args, **kwargs)
        duration = (time.monotonic() - started_at) * 1000
        _emit(SpanEvent(
            event_id=_next_event_id(),
            span_id=span_id,
            parent_id=parent_id,
            name=name,
            type="EXIT",
            ts=time.time(),
            duration_ms=duration,
            attrs={},
        ))
        return result
    except Exception as exc:
        duration = (time.monotonic() - started_at) * 1000
        _emit(SpanEvent(
            event_id=_next_event_id(),
            span_id=span_id,
            parent_id=parent_id,
            name=name,
            type="EXIT",
            ts=time.time(),
            duration_ms=duration,
            attrs={"error": repr(exc)[:300]},
        ))
        raise
    finally:
        _current_span_id.reset(tok)


async def _run_span_async(name: str, static_attrs: dict, func: Callable, args, kwargs):
    if not is_tracing():
        return await func(*args, **kwargs)

    parent_id = _current_span_id.get()
    span_id = f"sp-{uuid.uuid4().hex[:10]}"
    tok = _current_span_id.set(span_id)
    started_at = time.monotonic()

    _emit(SpanEvent(
        event_id=_next_event_id(),
        span_id=span_id,
        parent_id=parent_id,
        name=name,
        type="ENTER",
        ts=time.time(),
        attrs=dict(static_attrs),
    ))
    try:
        result = await func(*args, **kwargs)
        duration = (time.monotonic() - started_at) * 1000
        _emit(SpanEvent(
            event_id=_next_event_id(),
            span_id=span_id,
            parent_id=parent_id,
            name=name,
            type="EXIT",
            ts=time.time(),
            duration_ms=duration,
            attrs={},
        ))
        return result
    except Exception as exc:
        duration = (time.monotonic() - started_at) * 1000
        _emit(SpanEvent(
            event_id=_next_event_id(),
            span_id=span_id,
            parent_id=parent_id,
            name=name,
            type="EXIT",
            ts=time.time(),
            duration_ms=duration,
            attrs={"error": repr(exc)[:300]},
        ))
        raise
    finally:
        _current_span_id.reset(tok)


# ============================================================
# 动态属性（函数体内给当前 span 加 attr）
# ============================================================

def span_attr(**kwargs: Any) -> None:
    """给当前 span 加属性。无 trace 时静默丢弃。

    用法（函数体内）：
        @span("llm.call")
        def chat(self, messages):
            resp = self.client.create(messages=messages)
            span_attr(prompt_tokens=resp.usage.prompt_tokens,
                      completion_tokens=resp.usage.completion_tokens)
            return resp
    """
    if not is_tracing():
        return
    span_id = _current_span_id.get()
    if span_id is None:
        return
    _emit(SpanEvent(
        event_id=_next_event_id(),
        span_id=span_id,
        parent_id=None,  # ATTR 类型不需要 parent（前端合并到 span 卡片）
        name="span_attr",
        type="ATTR",
        ts=time.time(),
        attrs=dict(kwargs),
    ))


# ============================================================
# 辅助：长文本截断（避免 base64 / prompt 爆 jsonl）
# ============================================================

def preview(text: str | None, max_len: int = 200) -> str:
    """截断长文本做 *_preview 字段。"""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"…(+{len(text) - max_len})"


def get_current_run_id() -> str | None:
    return _current_run_id.get()


def get_current_span_id() -> str | None:
    return _current_span_id.get()
