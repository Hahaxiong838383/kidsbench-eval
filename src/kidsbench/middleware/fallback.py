"""Fallback strategy primitives for capability declarations."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from kidsbench.contract import AdapterError, CapabilityLevel


@runtime_checkable
class FallbackStrategy(Protocol):
    """Protocol shared by all fallback strategies."""

    level: CapabilityLevel

    def explain(self) -> str:
        """Return a human-readable fallback explanation."""


@dataclass(frozen=True)
class WrappedFallback:
    """Inject and recover ``turn_id`` through metadata wrapping."""

    level: CapabilityLevel = "wrapped"

    def explain(self) -> str:
        """Describe wrapped fallback strategy."""
        return "metadata.turn_id inject + retrieve"

    def inject_turn_id(self, metadata: dict[str, Any], turn_id: str) -> dict[str, Any]:
        """Return copied metadata with ``turn_id`` injected."""
        wrapped = dict(metadata)
        wrapped["turn_id"] = turn_id
        return wrapped

    def extract_turn_ids(self, metadata: dict[str, Any]) -> list[str]:
        """Extract normalized turn IDs from wrapped metadata."""
        value = metadata.get("turn_id")
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str)]
        return []


@dataclass(frozen=True)
class ComputedFallback:
    """Resolve turn IDs by cosine similarity over embeddings."""

    score_threshold: float = 0.85
    level: CapabilityLevel = "computed"

    def explain(self) -> str:
        """Describe computed fallback strategy."""
        return f"cosine reverse lookup with threshold {self.score_threshold:.2f}"

    def resolve_turn_ids(
        self,
        query_embedding: list[float],
        candidates: list[tuple[str, list[float]]],
        top_k: int = 3,
    ) -> list[str]:
        """Return best matching turn IDs from embedding candidates."""
        if not candidates or not query_embedding:
            return []

        q = np.array(query_embedding, dtype=float)
        q_norm = np.linalg.norm(q)
        if q_norm == 0.0:
            return []

        scored: list[tuple[str, float]] = []
        for turn_id, vector in candidates:
            v = np.array(vector, dtype=float)
            v_norm = np.linalg.norm(v)
            if v_norm == 0.0:
                continue
            score = float(np.dot(q, v) / (q_norm * v_norm))
            if score >= self.score_threshold:
                scored.append((turn_id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [turn_id for turn_id, _ in scored[:top_k]]


@dataclass(frozen=True)
class SimulatedFallback:
    """Expose explicit estimate function with bounded error declaration."""

    estimate_fn: Callable[..., Any]
    error_bound_pct: float
    level: CapabilityLevel = "simulated"

    def __post_init__(self) -> None:
        if self.error_bound_pct < 0 or self.error_bound_pct > 100:
            raise AdapterError("error_bound_pct must be within [0, 100]")

    def explain(self) -> str:
        """Describe simulated fallback strategy and error bound."""
        return f"empirical estimate, error bound ±{self.error_bound_pct:.2f}%"

    def estimate(self, *args: Any, **kwargs: Any) -> Any:
        """Run estimation callback."""
        return self.estimate_fn(*args, **kwargs)


@dataclass(frozen=True)
class DeclaredFallback:
    """Declare unsupported capability with explicit empty output."""

    reason: str = "not supported"
    level: CapabilityLevel = "declared"

    def explain(self) -> str:
        """Describe declared fallback strategy."""
        return self.reason

    def value(self) -> None:
        """Return explicit empty output."""
        return None
