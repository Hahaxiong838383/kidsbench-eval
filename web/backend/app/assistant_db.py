"""AI 助手 SQLite 层：授权手机号、用量流水、运行设置。

设计：
- 只用 sqlite3 stdlib，部署时不引入额外数据库依赖
- 每次操作短连接 + 进程内锁，避免 FastAPI 多线程下共享连接踩线程问题
- init_db 可显式调用；业务函数也会惰性初始化当前 env 指向的库
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_INITIALIZED: set[str] = set()
_PHONE_RE = re.compile(r"^1\d{10}$")

DEFAULT_SETTINGS = {
    "assistant_enabled": "1",
    "daily_global_budget_tokens": "20000000",
    "tier_simple_model": "deepseek-v4-flash",
    "tier_diagnosis_model": "qwen3.6",
    "tier_upgrade_model": "gateway-gpt-5.5",
}


def _db_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.environ.get("KIDSBENCH_ASSISTANT_DB", "data/assistant.db")
    return Path(raw).expanduser()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_bounds() -> tuple[str, str]:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def _validate_phone(phone: str) -> str:
    if not _PHONE_RE.fullmatch(phone):
        raise ValueError("手机号必须是 11 位中国大陆手机号")
    return phone


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | os.PathLike[str] | None = None) -> None:
    """建表并写入默认 settings。重复调用安全。"""
    db_path = _db_path(path)
    key = str(db_path.resolve())
    with _LOCK:
        with _connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS phones (
                  phone TEXT PRIMARY KEY,
                  label TEXT DEFAULT '',
                  enabled INTEGER DEFAULT 1,
                  daily_quota_tokens INTEGER DEFAULT 2000000,
                  daily_upgrade_limit INTEGER DEFAULT 5,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_log (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  phone TEXT NOT NULL,
                  ts TEXT NOT NULL,
                  tier TEXT NOT NULL,
                  model TEXT NOT NULL,
                  tokens_in INTEGER,
                  tokens_out INTEGER,
                  latency_ms REAL,
                  user_forced INTEGER DEFAULT 0,
                  degraded INTEGER DEFAULT 0,
                  qhash TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                """
            )
            conn.executemany(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                DEFAULT_SETTINGS.items(),
            )
            # 幂等清理：前端解包 bug 曾把假键 items=[object Object] PATCH 进库
            # （2026-06-12 生产实锤），启动时清掉，防旧库残留
            conn.execute("DELETE FROM settings WHERE key = 'items'")
            conn.commit()
        _INITIALIZED.add(key)


def _ensure_db() -> Path:
    db_path = _db_path()
    key = str(db_path.resolve())
    if key not in _INITIALIZED:
        init_db(db_path)
    return db_path


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_phone(phone: str) -> dict[str, Any] | None:
    """读取单个授权手机号。"""
    _validate_phone(phone)
    with _LOCK, _connect(_ensure_db()) as conn:
        row = conn.execute("SELECT * FROM phones WHERE phone = ?", (phone,)).fetchone()
    return _row_to_dict(row)


def list_phones() -> list[dict[str, Any]]:
    """列出手机号，并带上今日已用 token，方便管理页直接展示。"""
    with _LOCK, _connect(_ensure_db()) as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   COALESCE(SUM(COALESCE(u.tokens_in, 0) + COALESCE(u.tokens_out, 0)), 0)
                     AS today_used_tokens
            FROM phones p
            LEFT JOIN usage_log u
              ON u.phone = p.phone AND u.ts >= ? AND u.ts < ?
            GROUP BY p.phone
            ORDER BY p.created_at DESC, p.phone ASC
            """,
            _today_bounds(),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_phone(
    phone: str,
    label: str = "",
    enabled: int | bool = 1,
    daily_quota_tokens: int = 2000000,
    daily_upgrade_limit: int = 5,
) -> dict[str, Any]:
    """新增或覆盖手机号配置；created_at 只在首次插入时生成。"""
    _validate_phone(phone)
    enabled_i = 1 if bool(enabled) else 0
    quota_i = max(0, int(daily_quota_tokens))
    upgrade_i = max(0, int(daily_upgrade_limit))
    with _LOCK, _connect(_ensure_db()) as conn:
        conn.execute(
            """
            INSERT INTO phones(
              phone, label, enabled, daily_quota_tokens, daily_upgrade_limit, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
              label = excluded.label,
              enabled = excluded.enabled,
              daily_quota_tokens = excluded.daily_quota_tokens,
              daily_upgrade_limit = excluded.daily_upgrade_limit
            """,
            (phone, label or "", enabled_i, quota_i, upgrade_i, _now_iso()),
        )
        conn.commit()
    found = get_phone(phone)
    if found is None:
        raise RuntimeError("手机号写入失败")
    return found


def delete_phone(phone: str) -> bool:
    """删除手机号；返回是否真的删除了一行。"""
    _validate_phone(phone)
    with _LOCK, _connect(_ensure_db()) as conn:
        cur = conn.execute("DELETE FROM phones WHERE phone = ?", (phone,))
        conn.commit()
        return cur.rowcount > 0


def log_usage(
    phone: str,
    tier: str,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: float | None = None,
    user_forced: int | bool = 0,
    degraded: int | bool = 0,
    qhash: str | None = None,
) -> None:
    """写一条用量流水；不存原始问题，只存 qhash。"""
    _validate_phone(phone)
    if tier not in {"simple", "diagnosis", "upgrade"}:
        raise ValueError("tier 必须是 simple / diagnosis / upgrade")
    with _LOCK, _connect(_ensure_db()) as conn:
        conn.execute(
            """
            INSERT INTO usage_log(
              phone, ts, tier, model, tokens_in, tokens_out,
              latency_ms, user_forced, degraded, qhash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                phone,
                _now_iso(),
                tier,
                model,
                max(0, int(tokens_in or 0)),
                max(0, int(tokens_out or 0)),
                latency_ms,
                1 if bool(user_forced) else 0,
                1 if bool(degraded) else 0,
                qhash,
            ),
        )
        conn.commit()


def today_usage(phone: str) -> dict[str, int]:
    """统计某手机号今日 token、请求数、手动升级次数。"""
    _validate_phone(phone)
    with _LOCK, _connect(_ensure_db()) as conn:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)), 0) AS tokens,
              COUNT(*) AS requests,
              COALESCE(SUM(CASE WHEN tier = 'upgrade' OR user_forced = 1 THEN 1 ELSE 0 END), 0)
                AS upgrades
            FROM usage_log
            WHERE phone = ? AND ts >= ? AND ts < ?
            """,
            (phone, *_today_bounds()),
        ).fetchone()
    return {"tokens": int(row["tokens"]), "requests": int(row["requests"]),
            "upgrades": int(row["upgrades"])}


def usage_summary(days: int = 7) -> list[dict[str, Any]]:
    """按 phone x day 聚合，供管理后台查看趋势。"""
    safe_days = min(max(int(days), 1), 90)
    since = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
             - timedelta(days=safe_days - 1)).isoformat(timespec="seconds")
    with _LOCK, _connect(_ensure_db()) as conn:
        rows = conn.execute(
            """
            SELECT
              phone,
              substr(ts, 1, 10) AS day,
              COALESCE(SUM(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)), 0) AS tokens,
              COUNT(*) AS requests,
              COALESCE(SUM(CASE WHEN tier = 'upgrade' OR user_forced = 1 THEN 1 ELSE 0 END), 0)
                AS upgrades,
              COALESCE(SUM(CASE WHEN degraded = 1 THEN 1 ELSE 0 END), 0) AS degraded
            FROM usage_log
            WHERE ts >= ?
            GROUP BY phone, day
            ORDER BY day DESC, phone ASC
            """,
            (since,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_setting(key: str) -> str | None:
    if not key:
        raise ValueError("setting key 不能为空")
    with _LOCK, _connect(_ensure_db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def list_settings() -> dict[str, str]:
    """返回 settings 表全部键值；管理后台需要看到 patch 后的新键。"""
    with _LOCK, _connect(_ensure_db()) as conn:
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key ASC").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def set_setting(key: str, value: str) -> None:
    if not key:
        raise ValueError("setting key 不能为空")
    with _LOCK, _connect(_ensure_db()) as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
