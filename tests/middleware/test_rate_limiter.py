from __future__ import annotations

import time

import pytest

from kidsbench.contract import AdapterError
from kidsbench.middleware.rate_limiter import GlobalRateLimiter, TokenBucketLimiter


def test_token_bucket_try_acquire_boundary() -> None:
    limiter = TokenBucketLimiter(rate_per_sec=10.0, burst=2)
    assert limiter.try_acquire(1) is True
    assert limiter.try_acquire(1) is True
    assert limiter.try_acquire(1) is False


def test_token_bucket_acquire_blocks_until_refill() -> None:
    limiter = TokenBucketLimiter(rate_per_sec=5.0, burst=1)
    limiter.acquire(1)
    t0 = time.perf_counter()
    limiter.acquire(1)
    elapsed = time.perf_counter() - t0
    assert elapsed >= 0.15


def test_token_bucket_rejects_over_burst_request() -> None:
    limiter = TokenBucketLimiter(rate_per_sec=10.0, burst=2)
    with pytest.raises(AdapterError):
        limiter.acquire(3)


def test_global_rate_limiter_register_and_acquire() -> None:
    gl = GlobalRateLimiter()
    gl.register("dashscope", rate=20.0, burst=2)
    assert gl.try_acquire("dashscope", 1) is True


def test_global_rate_limiter_unknown_provider() -> None:
    gl = GlobalRateLimiter()
    with pytest.raises(AdapterError):
        gl.acquire("missing")
