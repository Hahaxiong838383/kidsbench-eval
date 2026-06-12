"""AI 助手授权：HMAC token、配额闸、轻量限速。

设计：
- token 格式按契约固定为 base64url(payload_json).hex_hmac，只有 stdlib 依赖
- 配额判断只读 SQLite 聚合结果，chat 端实际用量结束后再由 assistant.py 落账
- rate_limit 是进程内滑动窗口；多 worker 部署时各 worker 独立计数，只能做轻量防枚举
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any

from . import assistant_db

_LIMIT_LOCK = threading.Lock()
_HITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_KINDS = {"session", "admin"}


def _secret() -> bytes:
    raw = os.environ.get("ASSISTANT_TOKEN_SECRET", "")
    if not raw:
        raise RuntimeError("ASSISTANT_TOKEN_SECRET 未配置")
    return raw.encode("utf-8")


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def make_token(phone_or_admin: str, kind: str, ttl_s: int) -> str:
    """签发 session/admin token。payload 字段名按契约统一叫 phone。"""
    if kind not in _KINDS:
        raise ValueError("kind 必须是 session 或 admin")
    if not phone_or_admin:
        raise ValueError("token subject 不能为空")
    if ttl_s <= 0:
        raise ValueError("ttl_s 必须为正数")
    payload = {
        "phone": phone_or_admin,
        "exp": int(time.time()) + int(ttl_s),
        "kind": kind,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = _b64_encode(raw)
    sig = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str, kind: str) -> dict[str, Any] | None:
    """校验 token；任何格式错误、签名错误、过期都返回 None。"""
    if kind not in _KINDS or not token:
        return None
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64_decode(body))
    except Exception:
        return None
    if payload.get("kind") != kind:
        return None
    if not isinstance(payload.get("phone"), str):
        return None
    if int(payload.get("exp", 0)) <= int(time.time()):
        return None
    return payload


def check_phone_quota(phone: str) -> dict[str, int | bool]:
    """返回手机号今日剩余 token、剩余强模型次数，以及是否允许继续请求。"""
    row = assistant_db.get_phone(phone)
    if row is None or int(row.get("enabled", 0)) != 1:
        return {"quota_left": 0, "upgrades_left": 0, "allowed": False}
    used = assistant_db.today_usage(phone)
    quota_left = max(0, int(row["daily_quota_tokens"]) - int(used["tokens"]))
    upgrades_left = max(0, int(row["daily_upgrade_limit"]) - int(used["upgrades"]))
    return {
        "quota_left": quota_left,
        "upgrades_left": upgrades_left,
        "allowed": quota_left > 0,
    }


def check_global_budget() -> bool:
    """检查全局今日预算。settings 值异常时 fail-closed。"""
    try:
        budget = int(assistant_db.get_setting("daily_global_budget_tokens") or "0")
    except ValueError:
        return False
    if budget <= 0:
        return False
    total = sum(int(row.get("tokens", 0)) for row in assistant_db.usage_summary(days=1))
    return total < budget


def rate_limit(ip: str, key: str, max_n: int, window_s: int) -> bool:
    """进程内滑动窗口限速；返回 True 表示放行。"""
    if max_n <= 0 or window_s <= 0:
        return False
    ident = (ip or "unknown", key or "default")
    now = time.monotonic()
    with _LIMIT_LOCK:
        hits = _HITS[ident]
        while hits and now - hits[0] >= window_s:
            hits.popleft()
        if len(hits) >= max_n:
            return False
        hits.append(now)
        return True
