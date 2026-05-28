"""Structured logging helpers for adapters."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StructuredLogger:
    """Emit structured events to stdout and optional JSONL file."""

    def __init__(
        self,
        adapter_name: str,
        run_id: str | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self._run_id = run_id
        self._file_lock = threading.Lock()
        self._jsonl_path: Path | None = None

        if run_id is not None:
            base = log_dir or Path("runs")
            run_dir = base / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = run_dir / f"{adapter_name}.jsonl"

        self._logger = logging.getLogger(f"kidsbench.{adapter_name}.{run_id or 'norun'}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not any(isinstance(h, logging.StreamHandler) for h in self._logger.handlers):
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def info(self, event: str, **kwargs: Any) -> None:
        """Log info-level structured event."""
        self._emit("info", event, **kwargs)

    def warn(self, event: str, **kwargs: Any) -> None:
        """Log warning-level structured event."""
        self._emit("warn", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        """Log error-level structured event."""
        self._emit("error", event, **kwargs)

    def jsonl_path(self) -> Path | None:
        """Return JSONL output path, if file output is enabled."""
        return self._jsonl_path

    def _emit(self, level: str, event: str, **kwargs: Any) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "adapter": self._adapter_name,
            "run_id": self._run_id,
            "event": event,
            **kwargs,
        }
        line = json.dumps(payload, ensure_ascii=False)
        level_fn = {
            "info": self._logger.info,
            "warn": self._logger.warning,
            "error": self._logger.error,
        }.get(level, self._logger.info)
        level_fn(line)

        if self._jsonl_path is None:
            return
        with self._file_lock:
            with self._jsonl_path.open("a", encoding="utf-8") as fp:
                fp.write(line)
                fp.write("\n")
