"""AI 助手授权与管理后台 API。

这里导出独立 router，等待主应用统一接线。按契约，POST /api/assistant/session
也放在本文件实现：它属于授权入口，但前缀必须是 /api/assistant。
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from . import assistant_db
from .assistant_auth import make_token, rate_limit, verify_token

router = APIRouter(tags=["assistant-admin"])

_PHONE_RE = re.compile(r"^1\d{10}$")
_ADMIN_TTL_S = 24 * 60 * 60
_SESSION_TTL_S = 7 * 24 * 60 * 60


def _mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}" if _PHONE_RE.fullmatch(phone) else "invalid"


def _client_ip(request: Request) -> str:
    # 反代场景优先取首个 XFF；只用于限速，不参与鉴权。
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",", 1)[0].strip()
            or request.client.host if request.client else "unknown")


def _admin_hash() -> str:
    value = os.environ.get("ASSISTANT_ADMIN_PASSWORD_SHA256", "").strip().lower()
    if not value:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "管理后台未配置")
    return value


def _require_admin(request: Request) -> dict[str, Any]:
    _admin_hash()
    # 公网外层 Basic Auth 占用 Authorization header，API token 走自定义头；
    # Bearer 仅本地 dev 兜底（2026-06-12 公网验证实战发现的 header 冲突）
    token = request.headers.get("X-Kidsbench-Token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
    payload = verify_token(token, "admin") if token else None
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "管理员登录已过期")
    return payload


def _expires_at(ttl_s: int) -> str:
    return datetime.fromtimestamp(datetime.now().timestamp() + ttl_s).isoformat(timespec="seconds")


def _phone_or_422(phone: str) -> str:
    if not _PHONE_RE.fullmatch(phone):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "手机号格式必须是 11 位数字")
    return phone


class LoginBody(BaseModel):
    """管理员登录请求。密码只参与哈希比对，绝不写日志或回显。"""

    password: str = Field(min_length=1)


class SessionBody(BaseModel):
    """用户侧手机号换 session token。"""

    phone: str = Field(pattern=r"^1\d{10}$")


class PhoneCreateBody(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")
    label: str = Field(default="", max_length=80)
    enabled: bool = True
    daily_quota_tokens: int = Field(default=2000000, ge=0)
    daily_upgrade_limit: int = Field(default=5, ge=0)


class PhonePatchBody(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    daily_quota_tokens: int | None = Field(default=None, ge=0)
    daily_upgrade_limit: int | None = Field(default=None, ge=0)

    @field_validator("label")
    @classmethod
    def _label_not_none_string(cls, value: str | None) -> str | None:
        return value if value is None else value.strip()


@router.post("/api/assistant/session")
def create_session(body: SessionBody, request: Request) -> dict:
    """手机号白名单换取会话 token。限速是为了防枚举授权号码。"""
    if not rate_limit(_client_ip(request), "assistant_session", 10, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "请求过于频繁，请稍后再试")
    row = assistant_db.get_phone(body.phone)
    if row is None or int(row.get("enabled", 0)) != 1:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该手机号未被授权，请联系管理员")
    token = make_token(body.phone, "session", _SESSION_TTL_S)
    return {"token": token, "expires_at": _expires_at(_SESSION_TTL_S), "label": row.get("label", "")}


@router.post("/api/admin/login")
def admin_login(body: LoginBody, request: Request) -> dict:
    """管理员登录：明文密码只在内存里做 sha256 后比较。"""
    expected = _admin_hash()
    if not rate_limit(_client_ip(request), "admin_login", 5, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "请求过于频繁，请稍后再试")
    actual = hashlib.sha256(body.password.encode("utf-8")).hexdigest()
    if actual != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码错误")
    token = make_token("admin", "admin", _ADMIN_TTL_S)
    return {"token": token, "expires_at": _expires_at(_ADMIN_TTL_S)}


@router.get("/api/admin/phones")
def admin_list_phones(request: Request) -> dict:
    _require_admin(request)
    return {"items": assistant_db.list_phones()}


@router.post("/api/admin/phones", status_code=201)
def admin_create_phone(body: PhoneCreateBody, request: Request) -> dict:
    _require_admin(request)
    return assistant_db.upsert_phone(
        phone=body.phone,
        label=body.label.strip(),
        enabled=body.enabled,
        daily_quota_tokens=body.daily_quota_tokens,
        daily_upgrade_limit=body.daily_upgrade_limit,
    )


@router.patch("/api/admin/phones/{phone}")
def admin_update_phone(phone: str, body: PhonePatchBody, request: Request) -> dict:
    _require_admin(request)
    phone = _phone_or_422(phone)
    existing = assistant_db.get_phone(phone)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "手机号不存在")
    return assistant_db.upsert_phone(
        phone=phone,
        label=body.label if body.label is not None else existing.get("label", ""),
        enabled=body.enabled if body.enabled is not None else bool(existing.get("enabled", 1)),
        daily_quota_tokens=(
            body.daily_quota_tokens
            if body.daily_quota_tokens is not None
            else int(existing.get("daily_quota_tokens", 2000000))
        ),
        daily_upgrade_limit=(
            body.daily_upgrade_limit
            if body.daily_upgrade_limit is not None
            else int(existing.get("daily_upgrade_limit", 5))
        ),
    )


@router.delete("/api/admin/phones/{phone}", status_code=204, response_class=Response)
def admin_delete_phone(phone: str, request: Request) -> Response:
    _require_admin(request)
    if not assistant_db.delete_phone(_phone_or_422(phone)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "手机号不存在")
    return Response(status_code=204)


@router.get("/api/admin/usage")
def admin_usage(request: Request, days: int = 7) -> dict:
    _require_admin(request)
    if days < 1 or days > 90:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "days 必须在 1 到 90 之间")
    return {"items": assistant_db.usage_summary(days=days)}


@router.get("/api/admin/settings")
def admin_settings(request: Request) -> dict:
    _require_admin(request)
    return {"items": assistant_db.list_settings()}


@router.patch("/api/admin/settings")
async def admin_patch_settings(request: Request) -> dict:
    _require_admin(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "请求体必须是键值对象")
    for key, value in body.items():
        if not isinstance(key, str) or not key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "setting key 不能为空")
        assistant_db.set_setting(key, str(value))
    return {"items": assistant_db.list_settings()}


__all__ = ["router", "_mask_phone"]
