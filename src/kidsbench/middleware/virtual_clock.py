"""Virtual clock utilities for deterministic temporal evaluation."""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time


class VirtualClock:
    """Virtual time source for adapter logic."""

    def __init__(self, start: float | None = None) -> None:
        anchor = time.time() if start is None else float(start)
        self._lock = threading.Lock()
        self._anchor_virtual = anchor
        self._anchor_real = time.time()
        self._frozen = False

    def now(self) -> float:
        """Return current virtual timestamp."""
        with self._lock:
            return self._now_locked()

    def advance(self, seconds: float) -> None:
        """Advance virtual clock by seconds."""
        with self._lock:
            self._anchor_virtual = self._now_locked() + float(seconds)
            self._anchor_real = time.time()

    def jump_to(self, ts: float) -> None:
        """Jump virtual clock to exact timestamp."""
        with self._lock:
            self._anchor_virtual = float(ts)
            self._anchor_real = time.time()

    def freeze(self) -> None:
        """Freeze automatic progress."""
        with self._lock:
            self._anchor_virtual = self._now_locked()
            self._anchor_real = time.time()
            self._frozen = True

    def unfreeze(self) -> None:
        """Resume automatic progress from current virtual timestamp."""
        with self._lock:
            self._anchor_virtual = self._now_locked()
            self._anchor_real = time.time()
            self._frozen = False

    def _now_locked(self) -> float:
        if self._frozen:
            return self._anchor_virtual
        return self._anchor_virtual + (time.time() - self._anchor_real)

    @contextlib.contextmanager
    def as_context(self):
        """Bind this clock as current within context scope."""
        token = _current_clock.set(self)
        try:
            yield self
        finally:
            _current_clock.reset(token)


class _RealClock(VirtualClock):
    """Read-only real-time clock fallback."""

    def __init__(self) -> None:
        super().__init__(start=time.time())

    def now(self) -> float:
        return time.time()

    def advance(self, seconds: float) -> None:
        return None

    def jump_to(self, ts: float) -> None:
        return None

    def freeze(self) -> None:
        return None

    def unfreeze(self) -> None:
        return None


_current_clock: contextvars.ContextVar[VirtualClock | None] = contextvars.ContextVar(
    "current_virtual_clock", default=None
)
_REAL_CLOCK = _RealClock()


def get_clock() -> VirtualClock:
    """Return clock bound in context, or default real-time clock."""
    clock = _current_clock.get()
    return clock if clock is not None else _REAL_CLOCK
