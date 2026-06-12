"""B0 后端核心 endpoint 测试。"""


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["phase"] == "B0"


def test_root_lists_endpoints(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert "endpoints" in body
    assert "/healthz" in body["endpoints"]["health"]


def test_architecture_overview(client):
    response = client.get("/api/architecture")
    assert response.status_code == 200
    body = response.json()
    assert set(body["adapters"].keys()) == {"mem0", "memoryos", "graphiti", "hindsight", "reme", "letta"}
    assert body["embedding_model"]["name"] == "BAAI/bge-small-zh-v1.5"


def test_architecture_adapter_detail(client):
    response = client.get("/api/architecture/adapter/mem0")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Mem0"
    assert body["sdk"]["package"] == "mem0ai"


def test_architecture_adapter_404(client):
    response = client.get("/api/architecture/adapter/nonexistent_sys")
    assert response.status_code == 404


def test_runs_groups_lists_two(client):
    response = client.get("/api/runs/groups")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    group_names = {g["name"] for g in body["items"]}
    assert group_names == {"mem0_bge", "memoryos_bge"}


def test_runs_groups_filter_by_adapter(client):
    response = client.get("/api/runs/groups?adapter=mem0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "mem0_bge"


def test_runs_groups_filter_by_era(client):
    response = client.get("/api/runs/groups?era=after_bge")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2


def test_runs_group_detail(client):
    response = client.get("/api/runs/groups/mem0_bge")
    assert response.status_code == 200
    body = response.json()
    assert body["target_adapter"] == "mem0"
    assert body["results_count"] == 2  # mem0 + nomemory 两行
    assert body["summary"]["mem0"]["correct"] == 6


def test_runs_group_detail_404(client):
    response = client.get("/api/runs/groups/nonexistent")
    assert response.status_code == 404


def test_experiments_flat_list(client):
    response = client.get("/api/runs/experiments")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3  # mem0_bge 2 行 + memoryos_bge 1 行


def test_experiments_filter_by_adapter(client):
    response = client.get("/api/runs/experiments?adapter=mem0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["adapter"] == "mem0"


def test_experiments_filter_by_verdict(client):
    response = client.get("/api/runs/experiments?verdict=evasive")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1


def test_runs_latest_per_adapter(client):
    response = client.get("/api/runs/latest")
    assert response.status_code == 200
    body = response.json()
    # 至少有 mem0 / memoryos / nomemory
    adapters = {item["adapter"] for item in body["items"]}
    assert "mem0" in adapters
    assert "memoryos" in adapters


def test_state_mem0_from_latest_row(client):
    response = client.get("/api/state/mem0")
    assert response.status_code == 200
    body = response.json()
    assert body["adapter"] == "mem0"
    assert body["real_time"] is False
    snapshot = body["snapshot"]
    assert snapshot is not None
    assert snapshot["latest_row"]["adapter"] == "mem0"


def test_state_graphiti_graceful_fallback(client):
    """FalkorDB 不可达时优雅降级：返回 200 + warning + fallback snapshot，不再 503。"""
    response = client.get("/api/state/graphiti")
    assert response.status_code == 200
    body = response.json()
    assert body["adapter"] == "graphiti"
    # 测试环境无 tunnel：必有 warning，real_time=False
    # 若 tunnel 真开了：real_time=True，warning 不存在
    if body.get("warning"):
        assert body["real_time"] is False
        assert "FalkorDB" in body["warning"] or "ssh" in body["warning"].lower()
    else:
        assert body["real_time"] is True
        assert body.get("graphs") is not None


# ============= 题库板块（2026-06-11）=============


def test_questionbank_overview(client):
    response = client.get("/api/questionbank")
    assert response.status_code == 200
    body = response.json()
    assert body["bank_version"] == "v0.1_记忆"
    assert body["health"]["total_in_jsonl"] >= 140
    # 管线与 harness 白话说明必须在（显性化要求）
    assert len(body["pipeline"]) == 7
    assert all("plain" in s and "term" in s for s in body["pipeline"])
    assert "plain" in body["harness"]
    assert len(body["harness"]["steps"]) == 6


def test_questionbank_fixes(client):
    response = client.get("/api/questionbank/fixes")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 60
    first = body["items"][0]
    assert all(k in first for k in ("qid", "problem", "diagnosis", "fix"))


def test_questionbank_question_detail(client):
    response = client.get("/api/questionbank/questions/S04-④-002")
    assert response.status_code == 200
    body = response.json()
    assert body["question"]["qid"] == "S04-④-002"
    assert body["question"]["gold_memory_ids"]


def test_questionbank_question_404(client):
    assert client.get("/api/questionbank/questions/NOPE-001").status_code == 404


def test_questionbank_upload_rejects_non_csv(client):
    response = client.post(
        "/api/questionbank/upload",
        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")})
    assert response.status_code == 400


def test_questionbank_upload_roundtrip(client, tmp_path):
    """上传当前快照 CSV → 应转换成功且不报新增 issue。"""
    from app.config import QUESTIONS_PATH
    raw = next((QUESTIONS_PATH / "raw").glob("*.csv"))
    response = client.post(
        "/api/questionbank/upload",
        files={"file": (raw.name, raw.read_bytes(), "text/csv")})
    assert response.status_code == 200
    body = response.json()
    assert body["healthy_questions"] >= 140
    assert body["issues_total"] == 0


def test_questionbank_export_analysis(client):
    response = client.get("/api/questionbank/export-analysis")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    text = response.text
    # 报告必须含人话章节（显性化要求）
    for needle in ("题库与评测分析报告", "题库的问题与改进", "改善建议与论证", "三条铁律"):
        assert needle in text


def test_questionbank_leaderboard(client):
    response = client.get("/api/questionbank/leaderboard")
    assert response.status_code == 200
    body = response.json()
    # 本地 runs 有 v01_full_* 数据时应有完整榜单与发现
    if body["board"]:
        first = body["board"][0]
        assert all(k in first for k in ("adapter", "avg_score", "plain", "correct"))
        assert isinstance(body["findings"], list)
        # 榜单按平均分降序
        scores = [b["avg_score"] for b in body["board"]]
        assert scores == sorted(scores, reverse=True)


def test_leaderboard_history(client):
    response = client.get("/api/questionbank/leaderboard/history")
    assert response.status_code == 200
    body = response.json()
    assert "snapshots" in body and "matrix" in body
    if body["total"] > 0:
        m = body["matrix"]
        assert m["adapters"] and m["columns"]
        # 每个系统的 cells 与列数对齐
        for a in m["adapters"]:
            assert len(m["cells"][a]) == len(m["columns"])


def test_paradigm_coverage(client):
    response = client.get("/api/questionbank/paradigm-coverage")
    assert response.status_code == 200
    body = response.json()
    assert len(body["home_ground"]) >= 6  # 含待接入的 hipporag2
    # T3/T5/T7 当前必然不足/缺席 → 必有补题建议
    assert body["suggestions"]
    statuses = {c["task_type"]: c["status"] for c in body["coverage"]}
    assert statuses["T5_longterm"] == "缺席"
    assert statuses["T3_update"] == "不足"


def test_questionbank_verdict_explained(client):
    body = client.get("/api/questionbank").json()
    v = body["verdict"]
    assert len(v["states"]) == 3
    keys = {s["key"] for s in v["states"]}
    assert keys == {"correct", "wrong", "evasive"}
    assert v["evasive_meaning"] and v["why_so_many"]
