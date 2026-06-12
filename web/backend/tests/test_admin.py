"""AI 助手管理后台契约测试。"""

import hashlib
import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import assistant_db


def _client() -> TestClient:
    from app import admin

    importlib.reload(admin)
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app)


def _setup(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("KIDSBENCH_ASSISTANT_DB", str(tmp_path / "assistant.db"))
    monkeypatch.setenv("ASSISTANT_TOKEN_SECRET", "test-secret")
    assistant_db.init_db()
    return _client()


def _admin_headers(client: TestClient, password: str = "pw") -> dict[str, str]:
    token = client.post("/api/admin/login", json={"password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_admin_unconfigured_returns_503(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    monkeypatch.delenv("ASSISTANT_ADMIN_PASSWORD_SHA256", raising=False)
    response = client.post("/api/admin/login", json={"password": "pw"})
    assert response.status_code == 503
    assert response.json()["detail"] == "管理后台未配置"

    response = client.get("/api/admin/phones", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 503


def test_admin_wrong_password_401(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("ASSISTANT_ADMIN_PASSWORD_SHA256", hashlib.sha256(b"pw").hexdigest())
    response = client.post("/api/admin/login", json={"password": "bad"})
    assert response.status_code == 401


def test_session_whitelist_and_validation(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    assistant_db.upsert_phone("13800001234", label="川哥")

    ok = client.post("/api/assistant/session", json={"phone": "13800001234"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["label"] == "川哥"
    assert body["token"]

    forbidden = client.post("/api/assistant/session", json={"phone": "13800001235"})
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "该手机号未被授权，请联系管理员"

    invalid = client.post("/api/assistant/session", json={"phone": "123"})
    assert invalid.status_code == 422


def test_admin_crud_full_chain(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("ASSISTANT_ADMIN_PASSWORD_SHA256", hashlib.sha256(b"pw").hexdigest())
    headers = _admin_headers(client)

    created = client.post(
        "/api/admin/phones",
        headers=headers,
        json={
            "phone": "13800001234",
            "label": "测试号",
            "daily_quota_tokens": 1000,
            "daily_upgrade_limit": 3,
        },
    )
    assert created.status_code == 201
    assert created.json()["phone"] == "13800001234"

    assistant_db.log_usage(
        "13800001234", tier="simple", model="deepseek-v4-flash", tokens_in=20, tokens_out=30
    )
    items = client.get("/api/admin/phones", headers=headers).json()["items"]
    assert items[0]["today_used_tokens"] == 50

    patched = client.patch(
        "/api/admin/phones/13800001234",
        headers=headers,
        json={"label": "新备注", "enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == "新备注"
    assert patched.json()["enabled"] == 0

    usage = client.get("/api/admin/usage?days=7", headers=headers)
    assert usage.status_code == 200
    assert usage.json()["items"][0]["tokens"] == 50

    settings = client.patch(
        "/api/admin/settings",
        headers=headers,
        json={"assistant_enabled": "0", "daily_global_budget_tokens": "123"},
    )
    assert settings.status_code == 200
    assert settings.json()["items"]["assistant_enabled"] == "0"

    deleted = client.delete("/api/admin/phones/13800001234", headers=headers)
    assert deleted.status_code == 204
    missing = client.delete("/api/admin/phones/13800001234", headers=headers)
    assert missing.status_code == 404
