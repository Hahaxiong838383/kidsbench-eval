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
    assert set(body["adapters"].keys()) == {"mem0", "memoryos", "graphiti", "hindsight"}
    assert body["embedding_model"]["name"] == "BAAI/bge-small-zh-v1.5"


def test_architecture_adapter_detail(client):
    response = client.get("/api/architecture/adapter/mem0")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Mem0"
    assert body["sdk"]["package"] == "mem0ai"


def test_architecture_adapter_404(client):
    response = client.get("/api/architecture/adapter/letta")
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
