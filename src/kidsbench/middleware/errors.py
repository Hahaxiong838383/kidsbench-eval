"""Error mapping helpers for adapter middleware."""
from __future__ import annotations

import importlib
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from kidsbench.contract import AdapterError


class NetworkError(AdapterError):
    """Network transport or upstream availability error."""


class RateLimitError(AdapterError):
    """Remote provider rate limit error."""


class QuotaExceededError(AdapterError):
    """Remote provider quota exhausted."""


class AuthError(AdapterError):
    """Authentication/authorization error."""


class LogicError(AdapterError):
    """Internal logic error exposed as adapter error."""


class TimeoutError_(AdapterError):
    """Timeout error wrapped for harness classification."""


_F = TypeVar("_F", bound=Callable[..., Any])
_EXCEPTION_CACHE: dict[str, type[BaseException] | None] = {}


def _resolve_exception(path: str) -> type[BaseException] | None:
    """Resolve exception class from dotted import path lazily."""
    if path in _EXCEPTION_CACHE:
        return _EXCEPTION_CACHE[path]

    module_path, _, attr = path.rpartition(".")
    if not module_path or not attr:
        _EXCEPTION_CACHE[path] = None
        return None

    try:
        module = importlib.import_module(module_path)
    except Exception:  # noqa: BLE001
        _EXCEPTION_CACHE[path] = None
        return None

    exc_type = getattr(module, attr, None)
    if isinstance(exc_type, type) and issubclass(exc_type, BaseException):
        _EXCEPTION_CACHE[path] = exc_type
        return exc_type

    _EXCEPTION_CACHE[path] = None
    return None


def wrap_errors(mapping: dict[str, type[AdapterError]]) -> Callable[[_F], _F]:
    """Map third-party exceptions to ``AdapterError`` subclasses.

    ``mapping`` uses lazy string paths as keys to avoid import-time hard dependency.
    """

    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as err:  # noqa: BLE001
                for path, target in mapping.items():
                    exc_type = _resolve_exception(path)
                    if exc_type is not None and isinstance(err, exc_type):
                        raise target(str(err)) from err
                raise

        return wrapped  # type: ignore[return-value]

    return decorator
