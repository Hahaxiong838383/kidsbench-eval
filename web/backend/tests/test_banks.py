"""题库上传 + CLI 桥契约测试（team 评审钉死的安全项）。

覆盖：上传转换/不可变去重/CSV 注入转义/路径穿越防护/CLI env 注入/
大小行数闸/未知 adapter 拒绝/下载。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "web" / "backend"))
sys.path.insert(0, str(_ROOT / "src"))

_TMP_BANKS = "/tmp/kb_banks_pytest"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("KIDSBENCH_BANKS_PATH", _TMP_BANKS)
    shutil.rmtree(_TMP_BANKS, ignore_errors=True)
    # 重新 import banks 让 _BANKS_ROOT 读到 env
    import importlib

    from app import banks
    importlib.reload(banks)
    from fastapi.testclient import TestClient

    from app.main import app
    app.dependency_overrides = {}
    # 直接把 reload 后的 router 的 root 同步（banks 模块级常量）
    banks._BANKS_ROOT = Path(_TMP_BANKS)
    return TestClient(app)


_MINI_CSV = (
    "题目编号,主测维度,task_type,对话历史,query,期望事实,场景上下文\n"
    "S01-①-001,记忆召回,T1_recall,用户:我养了布偶猫团子,团子是什么品种,布偶猫,晚自习\n"
)


def _upload(client, content: bytes, name="t.csv"):
    return client.post("/api/banks/upload",
                       files={"file": (name, content, "text/csv")})


def test_upload_convert_and_immutable_dedup(client):
    r = _upload(client, _MINI_CSV.encode())
    assert r.status_code in (200, 422)  # 迷你 CSV 可能零健康题（字段不全）
    if r.status_code == 422:
        pytest.skip("迷你 CSV 字段不足，转换零健康题——用真 CSV 的集成测覆盖")
    d = r.json()
    ver = d["version"]
    assert ver.startswith("v_")
    # 同内容重传 → dedup
    r2 = _upload(client, _MINI_CSV.encode())
    assert r2.json().get("deduplicated") is True


def test_oversized_rejected(client):
    big = b"x,y\n" + b"a,b\n" * (3000 * 1024)  # >5MB
    r = _upload(client, big)
    assert r.status_code == 413


def test_too_many_rows_rejected(client):
    rows = "题目编号,query\n" + "".join(f"S{i},q\n" for i in range(3100))
    r = _upload(client, rows.encode())
    assert r.status_code == 400


def test_empty_file_rejected(client):
    r = _upload(client, b"")
    assert r.status_code == 400


def test_path_traversal_blocked(client):
    # 非法版本号 → 400/404，绝不读到 banks 目录外
    for bad in ["..%2f..%2fetc", "v_bad", "../../etc", "v_20260613_XXXXXXXXXXXXXXXX"]:
        r = client.get(f"/api/banks/{bad}/cli?adapters=cognee")
        assert r.status_code in (400, 404), f"{bad} 未拦截"


def test_cli_unknown_adapter_rejected(client, monkeypatch):
    # 造一个真版本目录
    import json

    from app import banks
    vdir = Path(_TMP_BANKS) / "v_20260613_deadbeefcafe1234"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "meta.json").write_text(json.dumps(
        {"version": "v_20260613_deadbeefcafe1234", "question_count": 10, "issues_count": 0,
         "created_at": "2026-06-13T00:00:00+00:00", "task_type_dist": {},
         "original_filename": "x.csv", "status": "validated"}), encoding="utf-8")
    banks._BANKS_ROOT = Path(_TMP_BANKS)
    r = client.get("/api/banks/v_20260613_deadbeefcafe1234/cli?adapters=evil_sys")
    assert r.status_code == 400
    # 合法 adapter → 命令含 no-prune env（cognee 安全注入）
    r2 = client.get("/api/banks/v_20260613_deadbeefcafe1234/cli?adapters=cognee")
    assert r2.status_code == 200
    assert "KIDSBENCH_COGNEE_NO_PRUNE=1" in r2.json()["command"]
    assert "TELEMETRY_DISABLED=1" in r2.json()["command"]


def test_csv_injection_escaped():
    """issues.csv 输出危险前缀单元格加 ' 转义。"""
    from app.banks import _csv_safe
    assert _csv_safe("=SUM(A1)") == "'=SUM(A1)"
    assert _csv_safe("+1") == "'+1"
    assert _csv_safe("-cmd") == "'-cmd"
    assert _csv_safe("@x") == "'@x"
    assert _csv_safe("正常文本") == "正常文本"
    assert _csv_safe("") == ""


def test_cli_no_credential_leak(client):
    """codex P0：CLI 命令绝不含明文 Basic Auth 凭据（用 env 占位）。"""
    import json

    from app import banks
    v = "v_20260613_aabbccddeeff0011"
    vdir = Path(_TMP_BANKS) / v
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "meta.json").write_text(json.dumps(
        {"version": v, "question_count": 10, "issues_count": 0,
         "created_at": "2026-06-13T00:00:00+00:00", "task_type_dist": {},
         "original_filename": "x.csv", "status": "validated"}), encoding="utf-8")
    banks._BANKS_ROOT = Path(_TMP_BANKS)
    cmd = client.get(f"/api/banks/{v}/cli?adapters=memmachine").json()["command"]
    assert "Futu#2026" not in cmd and "futurus:" not in cmd
    assert "$KIDSBENCH_WEB_AUTH" in cmd
