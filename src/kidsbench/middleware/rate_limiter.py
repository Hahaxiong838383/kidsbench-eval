"""Rate limiter primitives for external provider calls."""
from __future__ import annotations

import threading
import time

from kidsbench.contract import AdapterError


class TokenBucketLimiter:
    """Thread-safe token bucket limiter."""

    def __init__(self, rate_per_sec: float, burst: int) -> None:
        if rate_per_sec <= 0:
            raise AdapterError("rate_per_sec must be positive")
        if burst <= 0:
            raise AdapterError("burst must be positive")
        self._rate = float(rate_per_sec)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> None:
        """Block until requested tokens are available."""
        need = float(tokens)
        if need <= 0:
            return
        if need > self._burst:
            raise AdapterError("requested tokens exceed bucket burst")

        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= need:
                    self._tokens -= need
                    return
                missing = need - self._tokens
            sleep_for = max(missing / self._rate, 0.001)
            time.sleep(sleep_for)

    def try_acquire(self, tokens: int = 1) -> bool:
        """Try token acquire without blocking."""
        need = float(tokens)
        if need <= 0:
            return True
        if need > self._burst:
            return False

        with self._lock:
            self._refill_locked()
            if self._tokens < need:
                return False
            self._tokens -= need
            return True

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        if elapsed <= 0:
            return
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._updated_at = now


class GlobalRateLimiter:
    """Provider-keyed limiter manager."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: dict[str, TokenBucketLimiter] = {}

    def register(self, provider: str, rate: float, burst: int) -> None:
        """Register or replace a provider bucket."""
        if not provider:
            raise AdapterError("provider must not be empty")
        with self._lock:
            self._providers[provider] = TokenBucketLimiter(rate_per_sec=rate, burst=burst)

    def acquire(self, provider: str, tokens: int = 1) -> None:
        """Acquire tokens for one provider."""
        limiter = self._get(provider)
        limiter.acquire(tokens=tokens)

    def try_acquire(self, provider: str, tokens: int = 1) -> bool:
        """Try acquire tokens for one provider."""
        limiter = self._get(provider)
        return limiter.try_acquire(tokens=tokens)

    def _get(self, provider: str) -> TokenBucketLimiter:
        with self._lock:
            limiter = self._providers.get(provider)
        if limiter is None:
            raise AdapterError(f"rate limiter provider not registered: {provider}")
        return limiter
