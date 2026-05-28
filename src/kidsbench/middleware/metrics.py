"""Metrics collection helpers for adapter middleware."""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from functools import wraps
from typing import Any, TypeVar

from kidsbench.contract import AdapterError

_CTX_ADAPTER_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "metrics_adapter_name", default=None
)
_CTX_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "metrics_run_id", default=None
)

_F = TypeVar("_F", bound=Callable[..., Any])


@contextlib.contextmanager
def metrics_context(*, adapter_name: str | None = None, run_id: str | None = None):
    """Bind metrics context for the current execution context."""
    token_adapter = None
    token_run = None
    if adapter_name is not None:
        token_adapter = _CTX_ADAPTER_NAME.set(adapter_name)
    if run_id is not None:
        token_run = _CTX_RUN_ID.set(run_id)
    try:
        yield
    finally:
        if token_adapter is not None:
            _CTX_ADAPTER_NAME.reset(token_adapter)
        if token_run is not None:
            _CTX_RUN_ID.reset(token_run)


def set_metrics_context(*, adapter_name: str | None = None, run_id: str | None = None) -> None:
    """Set metrics context vars for current task/thread context."""
    if adapter_name is not None:
        _CTX_ADAPTER_NAME.set(adapter_name)
    if run_id is not None:
        _CTX_RUN_ID.set(run_id)


class MetricsCollector:
    """Thread-safe in-memory metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    def record(
        self,
        adapter_name: str,
        user_id: str,
        method: str,
        latency_ms: float,
        cost_token: int,
        success: bool,
        error_class: str | None = None,
    ) -> None:
        """Record one middleware event."""
        with self._lock:
            adapter_bucket = self._data.setdefault(adapter_name, {})
            user_bucket = adapter_bucket.setdefault(user_id, self._new_bucket())
            user_bucket["total_calls"] += 1
            user_bucket["total_latency_ms"] += latency_ms
            user_bucket["total_cost_token"] += max(cost_token, 0)
            user_bucket["run_id"] = _CTX_RUN_ID.get()
            if success:
                user_bucket["success_calls"] += 1
            else:
                user_bucket["error_calls"] += 1
                user_bucket["last_error_class"] = error_class

            method_bucket = user_bucket["methods"].setdefault(method, self._new_method_bucket())
            method_bucket["calls"] += 1
            method_bucket["latency_ms"] += latency_ms
            method_bucket["cost_token"] += max(cost_token, 0)
            if success:
                method_bucket["success"] += 1
            else:
                method_bucket["errors"] += 1

    def snapshot(self, adapter_name: str, user_id: str) -> dict[str, Any]:
        """Return metrics snapshot for one adapter/user pair."""
        with self._lock:
            user_bucket = self._data.get(adapter_name, {}).get(user_id)
            if user_bucket is None:
                return {
                    "adapter_name": adapter_name,
                    "user_id": user_id,
                    "total_calls": 0,
                    "success_calls": 0,
                    "error_calls": 0,
                    "total_latency_ms": 0.0,
                    "avg_latency_ms": 0.0,
                    "total_cost_token": 0,
                    "methods": {},
                    "run_id": None,
                    "last_error_class": None,
                }
            methods = {
                method: {
                    **stats,
                    "avg_latency_ms": (
                        stats["latency_ms"] / stats["calls"] if stats["calls"] else 0.0
                    ),
                }
                for method, stats in user_bucket["methods"].items()
            }
            calls = user_bucket["total_calls"]
            return {
                "adapter_name": adapter_name,
                "user_id": user_id,
                "total_calls": calls,
                "success_calls": user_bucket["success_calls"],
                "error_calls": user_bucket["error_calls"],
                "total_latency_ms": user_bucket["total_latency_ms"],
                "avg_latency_ms": user_bucket["total_latency_ms"] / calls if calls else 0.0,
                "total_cost_token": user_bucket["total_cost_token"],
                "methods": methods,
                "run_id": user_bucket["run_id"],
                "last_error_class": user_bucket["last_error_class"],
            }

    def reset(self, adapter_name: str | None = None, user_id: str | None = None) -> None:
        """Reset collected metrics by adapter/user scope."""
        with self._lock:
            if adapter_name is None:
                self._data.clear()
                return
            if adapter_name not in self._data:
                return
            if user_id is None:
                self._data.pop(adapter_name, None)
                return
            self._data[adapter_name].pop(user_id, None)

    @staticmethod
    def _new_bucket() -> dict[str, Any]:
        return {
            "total_calls": 0,
            "success_calls": 0,
            "error_calls": 0,
            "total_latency_ms": 0.0,
            "total_cost_token": 0,
            "methods": {},
            "run_id": None,
            "last_error_class": None,
        }

    @staticmethod
    def _new_method_bucket() -> dict[str, Any]:
        return {
            "calls": 0,
            "success": 0,
            "errors": 0,
            "latency_ms": 0.0,
            "cost_token": 0,
        }


METRICS = MetricsCollector()


def track_metrics(method: str) -> Callable[[_F], _F]:
    """Decorator that records method latency/cost into ``METRICS``."""

    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            adapter_name = _CTX_ADAPTER_NAME.get() or _get_adapter_name(args)
            user_id = _get_user_id(args, kwargs)
            try:
                result = func(*args, **kwargs)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                result = _replace_latency(result, latency_ms)
                METRICS.record(
                    adapter_name=adapter_name,
                    user_id=user_id,
                    method=method,
                    latency_ms=latency_ms,
                    cost_token=_extract_cost_token(result),
                    success=True,
                )
                return result
            except Exception as err:
                latency_ms = (time.perf_counter() - t0) * 1000.0
                METRICS.record(
                    adapter_name=adapter_name,
                    user_id=user_id,
                    method=method,
                    latency_ms=latency_ms,
                    cost_token=0,
                    success=False,
                    error_class=type(err).__name__,
                )
                raise

        return wrapped  # type: ignore[return-value]

    return decorator


def _get_adapter_name(args: tuple[Any, ...]) -> str:
    if args:
        obj = args[0]
        name = getattr(obj, "name", None)
        if isinstance(name, str) and name:
            return name
        return obj.__class__.__name__
    return "unknown"


def _get_user_id(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    user_id = kwargs.get("user_id")
    if isinstance(user_id, str):
        return user_id
    if len(args) > 1 and isinstance(args[1], str):
        return args[1]
    return ""


def _replace_latency(result: Any, latency_ms: float) -> Any:
    if hasattr(result, "latency_ms") and getattr(result, "__dataclass_fields__", None):
        return replace(result, latency_ms=latency_ms)
    return result


def _extract_cost_token(result: Any) -> int:
    value = getattr(result, "cost_token", 0)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    raise AdapterError("cost_token must be numeric")
