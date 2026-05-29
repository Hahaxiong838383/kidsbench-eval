"""TracedAdapter — 给 MemoryAdapter 加 trace 的透明 wrapper。

设计：用 __getattr__ delegate 模式，少写 boilerplate。
被 wrap 的方法（write/read/clear/flush/consolidate/batch_write）自动套 @span。
其他属性 / 方法（name / get_stats / get_capability_profile 等）原样透传。

注意：isinstance(wrapped, MemoryAdapter) == False，调用方不能依赖 isinstance 检查。
KidsBench harness 只调方法不查 isinstance，OK。
"""

from __future__ import annotations

from typing import Any

from .span import span as span_decorator

# 这些方法被自动 trace
_TRACED_METHODS = frozenset({
    "write",
    "read",
    "clear",
    "flush",
    "consolidate",
    "batch_write",
})


class TracedAdapter:
    """Transparent proxy: trace 几个核心方法，其他 delegate 到 inner。

    用法：
        traced = TracedAdapter(real_adapter)
        traced.write(user_id, turn)   # 自动 span("adapter.write", adapter=name)
        traced.read(user_id, query)   # 同上
        traced.name                   # delegate
    """

    def __init__(self, inner: Any) -> None:
        # 用 object.__setattr__ 避免触发 __setattr__ 路径
        object.__setattr__(self, "_inner", inner)
        # 缓存 wrapped 方法（避免每次 __getattr__ 重新装饰）
        object.__setattr__(self, "_cache", {})

    @property
    def name(self) -> str:
        return self._inner.name

    @property  # type: ignore[override]
    def __class__(self):  # noqa: D401 - 让 isinstance(traced, OriginalClass) 透传
        return self._inner.__class__

    def __getattr__(self, key: str) -> Any:
        # __getattr__ 只在 attribute 找不到时触发，
        # _inner / _cache / name 都走 __getattribute__ 直接到
        cache = object.__getattribute__(self, "_cache")
        if key in cache:
            return cache[key]
        attr = getattr(self._inner, key)
        if key in _TRACED_METHODS and callable(attr):
            wrapped = span_decorator(f"adapter.{key}", adapter=self._inner.name)(attr)
            cache[key] = wrapped
            return wrapped
        return attr

    def __repr__(self) -> str:
        return f"TracedAdapter({self._inner!r})"


def wrap(adapter: Any) -> Any:
    """便捷工厂：包一个 adapter 成 TracedAdapter。已经包过则原样返回。"""
    if isinstance(adapter, TracedAdapter):
        return adapter
    return TracedAdapter(adapter)
