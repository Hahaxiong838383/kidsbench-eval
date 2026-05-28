"""Turn-memory sidecar mapping with memory/sqlite backends."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from kidsbench.contract import AdapterError


class SidecarStore:
    """Maintain many-to-many mapping between turn IDs and memory IDs."""

    def __init__(self, backend: str = "memory", path: Path | None = None) -> None:
        self._backend = backend
        self._lock = threading.Lock()

        self._turn_index: dict[str, dict[str, set[str]]] = {}
        self._memory_index: dict[str, dict[str, set[str]]] = {}

        self._conn: sqlite3.Connection | None = None
        if backend == "sqlite":
            db_path = path or Path("runs") / "sidecar.sqlite3"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._init_sqlite(self._conn)
        elif backend != "memory":
            raise AdapterError(f"unsupported sidecar backend: {backend}")

    def put(self, user_id: str, turn_id: str, memory_ids: list[str]) -> None:
        """Insert bidirectional mapping for one turn and many memories."""
        if not user_id or not turn_id:
            raise AdapterError("user_id and turn_id must not be empty")
        if any(not mid for mid in memory_ids):
            raise AdapterError("memory_id must not be empty")
        if not memory_ids:
            return

        with self._lock:
            if self._backend == "memory":
                self._put_memory(user_id, turn_id, memory_ids)
                return
            self._put_sqlite(user_id, turn_id, memory_ids)

    def get_memory_ids(self, user_id: str, turn_id: str) -> list[str]:
        """Get mapped memory IDs by turn ID."""
        with self._lock:
            if self._backend == "memory":
                return sorted(self._turn_index.get(user_id, {}).get(turn_id, set()))
            rows = self._conn.execute(
                "SELECT memory_id FROM mapping WHERE user_id = ? AND turn_id = ?",
                (user_id, turn_id),
            ).fetchall()
            return sorted(row[0] for row in rows)

    def get_turn_ids(self, user_id: str, memory_id: str) -> list[str]:
        """Reverse lookup turn IDs by memory ID."""
        with self._lock:
            if self._backend == "memory":
                return sorted(self._memory_index.get(user_id, {}).get(memory_id, set()))
            rows = self._conn.execute(
                "SELECT turn_id FROM mapping WHERE user_id = ? AND memory_id = ?",
                (user_id, memory_id),
            ).fetchall()
            return sorted(row[0] for row in rows)

    def clear_user(self, user_id: str) -> int:
        """Clear all mappings for one user and return deleted edge count."""
        with self._lock:
            if self._backend == "memory":
                turn_map = self._turn_index.pop(user_id, {})
                self._memory_index.pop(user_id, None)
                return sum(len(values) for values in turn_map.values())

            cursor = self._conn.execute("DELETE FROM mapping WHERE user_id = ?", (user_id,))
            self._conn.commit()
            return int(cursor.rowcount or 0)

    def stats(self, user_id: str) -> dict[str, int]:
        """Return mapping stats for one user."""
        with self._lock:
            if self._backend == "memory":
                turn_map = self._turn_index.get(user_id, {})
                memory_map = self._memory_index.get(user_id, {})
                return {
                    "turn_count": len(turn_map),
                    "memory_count": len(memory_map),
                    "mapping_count": sum(len(v) for v in turn_map.values()),
                }

            row = self._conn.execute(
                """
                SELECT
                    COUNT(DISTINCT turn_id),
                    COUNT(DISTINCT memory_id),
                    COUNT(*)
                FROM mapping
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            return {
                "turn_count": int(row[0]),
                "memory_count": int(row[1]),
                "mapping_count": int(row[2]),
            }

    @staticmethod
    def _init_sqlite(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mapping (
                user_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                ts REAL NOT NULL,
                PRIMARY KEY (user_id, turn_id, memory_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mapping_user_turn ON mapping(user_id, turn_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mapping_user_memory ON mapping(user_id, memory_id)"
        )
        conn.commit()

    def _put_memory(self, user_id: str, turn_id: str, memory_ids: list[str]) -> None:
        turn_map = self._turn_index.setdefault(user_id, {})
        memory_map = self._memory_index.setdefault(user_id, {})
        turn_bucket = turn_map.setdefault(turn_id, set())
        for memory_id in memory_ids:
            turn_bucket.add(memory_id)
            memory_map.setdefault(memory_id, set()).add(turn_id)

    def _put_sqlite(self, user_id: str, turn_id: str, memory_ids: list[str]) -> None:
        now = time.time()
        rows = [(user_id, turn_id, memory_id, now) for memory_id in memory_ids]
        self._conn.executemany(
            "INSERT OR IGNORE INTO mapping(user_id, turn_id, memory_id, ts) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
