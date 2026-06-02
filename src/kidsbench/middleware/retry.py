import ssl
import sys
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

import httpx


def _is_retryable_error(exc: Exception) -> bool:
    """判断是否为可重试的瞬态网络错误。"""
    retryable_types = (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
        ssl.SSLError,
        ConnectionError,
    )
    if isinstance(exc, retryable_types):
        return True
    # 429 限流 + 5xx 网关（502/503/504）瞬态可重试；其余 4xx 是请求问题不重试
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


def retry_call(
    fn: Callable,
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    **kwargs: Any,
) -> Any:
    """使用指数退避重试调用 fn。

    只重试指定网络错误，非网络错误立即抛出。
    重试耗尽抛出最后一次异常，保留 traceback。
    每次重试前打印 stderr 告警。
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_retryable_error(exc):
                raise
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            print(
                f"重试 {attempt}/{max_attempts}，等待 {delay}s，错误: {type(exc).__name__}",
                file=sys.stderr,
            )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc


def with_retry(
    max_attempts: int = 3, base_delay: float = 0.5
) -> Callable[[Callable], Callable]:
    """装饰器：为函数添加 retry_call 行为。"""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return retry_call(
                fn, *args, max_attempts=max_attempts, base_delay=base_delay, **kwargs
            )

        return wrapper

    return decorator


if __name__ == "__main__":
    call_count = 0

    def flaky_connect() -> str:
        global call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("mock connect error")
        return "success"

    result = retry_call(flaky_connect, max_attempts=3, base_delay=0.1)
    assert result == "success", "第3次应成功"
    assert call_count == 3, "应调用3次"

    def immediate_value_error() -> None:
        raise ValueError("non-retryable")

    try:
        retry_call(immediate_value_error)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError 应立即抛出，不重试")

    print("自测通过", file=sys.stderr)
