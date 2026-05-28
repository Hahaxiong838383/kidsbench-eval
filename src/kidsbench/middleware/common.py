"""Shared middleware helpers."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any


def now_utc_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def elapsed_ms(start: float) -> float:
    """Return elapsed milliseconds based on ``time.perf_counter`` value."""
    return (time.perf_counter() - start) * 1000.0


def hash_text(text: str) -> str:
    """Return stable SHA256 hash for a text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_user_id(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Extract ``user_id`` from bound method call args/kwargs."""
    if "user_id" in kwargs and isinstance(kwargs["user_id"], str):
        return kwargs["user_id"]
    if len(args) > 1 and isinstance(args[1], str):
        return args[1]
    return ""
