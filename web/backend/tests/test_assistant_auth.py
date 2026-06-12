"""AI 助手授权基础能力测试。"""

import time

from app import assistant_db
from app.assistant_auth import (
    check_global_budget,
    check_phone_quota,
    make_token,
    rate_limit,
    verify_token,
)


def test_token_ok_tampered_expired(monkeypatch):
    monkeypatch.setenv("ASSISTANT_TOKEN_SECRET", "test-secret")

    token = make_token("13800001234", "session", 60)
    payload = verify_token(token, "session")
    assert payload is not None
    assert payload["phone"] == "13800001234"

    body, sig = token.split(".", 1)
    assert verify_token(f"{body}x.{sig}", "session") is None
    assert verify_token(make_token("13800001234", "session", 1), "admin") is None
    time.sleep(1.1)
    assert verify_token(make_token("13800001234", "session", 1), "session") is not None
    expired = make_token("13800001234", "session", 1)
    time.sleep(1.1)
    assert verify_token(expired, "session") is None


def test_quota_and_global_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("KIDSBENCH_ASSISTANT_DB", str(tmp_path / "assistant.db"))
    assistant_db.init_db()
    assistant_db.upsert_phone("13800001234", daily_quota_tokens=100, daily_upgrade_limit=2)

    assert check_phone_quota("13800001234") == {
        "quota_left": 100,
        "upgrades_left": 2,
        "allowed": True,
    }

    assistant_db.log_usage(
        "13800001234",
        tier="upgrade",
        model="gateway-gpt-5.5",
        tokens_in=30,
        tokens_out=20,
        user_forced=1,
    )
    quota = check_phone_quota("13800001234")
    assert quota["quota_left"] == 50
    assert quota["upgrades_left"] == 1
    assert quota["allowed"] is True

    assistant_db.set_setting("daily_global_budget_tokens", "40")
    assert check_global_budget() is False


def test_rate_limit_triggers():
    key = f"unit-{time.monotonic()}"
    assert rate_limit("127.0.0.1", key, 2, 60) is True
    assert rate_limit("127.0.0.1", key, 2, 60) is True
    assert rate_limit("127.0.0.1", key, 2, 60) is False
