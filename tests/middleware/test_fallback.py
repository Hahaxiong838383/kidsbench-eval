from __future__ import annotations

import pytest

from kidsbench.contract import AdapterError
from kidsbench.middleware.fallback import (
    ComputedFallback,
    DeclaredFallback,
    SimulatedFallback,
    WrappedFallback,
)


def test_wrapped_fallback_inject_and_extract() -> None:
    fb = WrappedFallback()
    md = fb.inject_turn_id({"x": 1}, "t1")
    assert md["turn_id"] == "t1"
    assert fb.extract_turn_ids(md) == ["t1"]


def test_computed_fallback_cosine_match() -> None:
    fb = ComputedFallback(score_threshold=0.8)
    ids = fb.resolve_turn_ids(
        [1.0, 0.0],
        [("t1", [0.99, 0.01]), ("t2", [0.0, 1.0])],
    )
    assert ids == ["t1"]


def test_computed_fallback_empty_inputs() -> None:
    fb = ComputedFallback()
    assert fb.resolve_turn_ids([], []) == []


def test_simulated_fallback_estimate_and_bound() -> None:
    fb = SimulatedFallback(lambda x: x + 1, error_bound_pct=3.5)
    assert fb.estimate(4) == 5
    assert "3.50" in fb.explain()


def test_simulated_fallback_invalid_bound() -> None:
    with pytest.raises(AdapterError):
        SimulatedFallback(lambda x: x, error_bound_pct=120)


def test_declared_fallback_returns_none() -> None:
    fb = DeclaredFallback(reason="unsupported")
    assert fb.value() is None
    assert fb.explain() == "unsupported"
