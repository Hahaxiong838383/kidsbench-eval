from __future__ import annotations

import time

import pytest

from kidsbench.middleware.virtual_clock import VirtualClock, get_clock


def test_virtual_clock_advance_and_jump() -> None:
    clock = VirtualClock(start=100.0)
    clock.freeze()
    assert clock.now() == pytest.approx(100.0, abs=0.01)
    clock.advance(10)
    assert clock.now() == pytest.approx(110.0, abs=0.01)
    clock.jump_to(200.0)
    assert clock.now() == pytest.approx(200.0, abs=0.01)


def test_virtual_clock_freeze_unfreeze() -> None:
    clock = VirtualClock(start=0.0)
    clock.unfreeze()
    t1 = clock.now()
    time.sleep(0.01)
    t2 = clock.now()
    assert t2 > t1

    clock.freeze()
    frozen = clock.now()
    time.sleep(0.01)
    assert clock.now() == frozen


def test_get_clock_context_binding() -> None:
    outside = get_clock()
    clock = VirtualClock(start=50.0)
    clock.freeze()
    with clock.as_context():
        inside = get_clock()
        assert inside.now() == pytest.approx(50.0, abs=0.01)
    assert get_clock() is outside
