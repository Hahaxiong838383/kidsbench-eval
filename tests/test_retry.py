"""网络重试模块测试（grok-4.3 写 + cc 加 5xx）。"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kidsbench.middleware import retry_call
from kidsbench.middleware.retry import _is_retryable_error

_REQ = httpx.Request("GET", "http://x")


def test_network_errors_retryable():
    assert _is_retryable_error(httpx.ConnectError("x"))
    assert _is_retryable_error(httpx.TimeoutException("x"))


def test_5xx_retryable():
    err = httpx.HTTPStatusError("x", request=_REQ, response=httpx.Response(503, request=_REQ))
    assert _is_retryable_error(err) is True


def test_4xx_not_retryable():
    err = httpx.HTTPStatusError("x", request=_REQ, response=httpx.Response(404, request=_REQ))
    assert _is_retryable_error(err) is False


def test_value_error_not_retryable():
    assert _is_retryable_error(ValueError("x")) is False


def test_retry_succeeds_after_transient():
    calls = [0]

    def flaky():
        calls[0] += 1
        if calls[0] < 3:
            raise httpx.ConnectError("transient")
        return "ok"

    assert retry_call(flaky, max_attempts=3, base_delay=0.01) == "ok"
    assert calls[0] == 3


def test_retry_exhausted_raises_last():
    def always_fail():
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        retry_call(always_fail, max_attempts=2, base_delay=0.01)


def test_non_retryable_raises_immediately():
    calls = [0]

    def bad():
        calls[0] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        retry_call(bad, max_attempts=3, base_delay=0.01)
    assert calls[0] == 1  # 没重试
