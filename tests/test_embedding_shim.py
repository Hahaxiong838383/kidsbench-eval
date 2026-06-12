"""embedding_shim 测试：用 TestClient 验证 OpenAI 兼容端点 + bge-small-zh 行为。

注意：测试会真实加载 BAAI/bge-small-zh-v1.5（本机 HF cache 命中即快），
按任务要求不加 @pytest.mark.slow，直接可跑。
"""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from kidsbench.middleware.embedding_shim import app

client = TestClient(app)


def test_single_input() -> None:
    """单条 input → data 长度 1、embedding 512 维、L2 范数≈1.0（normalize 验证）。"""
    resp = client.post(
        "/v1/embeddings",
        json={"model": "BAAI/bge-small-zh-v1.5", "input": "我家的猫叫团子"},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["object"] == "list"
    assert body["model"] == "BAAI/bge-small-zh-v1.5"
    assert "usage" in body
    assert isinstance(body["usage"]["prompt_tokens"], int)
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"]

    assert len(body["data"]) == 1
    item = body["data"][0]
    assert item["object"] == "embedding"
    assert item["index"] == 0
    emb = item["embedding"]
    assert isinstance(emb, list)
    assert len(emb) == 512
    assert all(isinstance(x, (int, float)) for x in emb)

    # 验证 normalize_embeddings=True 效果：L2 范数 ≈ 1.0
    norm = float(np.linalg.norm(emb))
    assert abs(norm - 1.0) < 1e-3, f"L2 norm should be ~1.0, got {norm}"


def test_batch_input() -> None:
    """批量 input 3 条 → data 长度 3、index 0/1/2 有序。"""
    texts = ["第一句测试", "第二句测试文本", "第三句用于批量验证"]
    resp = client.post(
        "/v1/embeddings",
        json={"model": "test-batch", "input": texts},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["data"]) == 3
    indices = [d["index"] for d in body["data"]]
    assert indices == [0, 1, 2]
    for i, d in enumerate(body["data"]):
        assert len(d["embedding"]) == 512
        assert d["index"] == i


def test_chinese_semantic_similarity() -> None:
    """中文语义 sanity：相似句余弦 > 不相似句。"""
    q1 = "我家的猫叫团子"
    q2 = "我的宠物猫名字是团子"  # 语义接近
    q3 = "明天考试好紧张"  # 语义不相关

    def _get_emb(text: str) -> np.ndarray:
        r = client.post(
            "/v1/embeddings",
            json={"model": "sem", "input": text},
        )
        assert r.status_code == 200
        return np.array(r.json()["data"][0]["embedding"], dtype=np.float32)

    e1 = _get_emb(q1)
    e2 = _get_emb(q2)
    e3 = _get_emb(q3)

    # 因 normalize，点积 = 余弦相似度
    sim12 = float(np.dot(e1, e2))
    sim13 = float(np.dot(e1, e3))

    assert sim12 > sim13, f"期望相似句 sim12({sim12:.4f}) > 不相似 sim13({sim13:.4f})"


def test_empty_input_returns_422() -> None:
    """空 input（空字符串或空列表）→ 422。"""
    # 空字符串
    r1 = client.post("/v1/embeddings", json={"model": "m", "input": ""})
    assert r1.status_code == 422

    # 空列表
    r2 = client.post("/v1/embeddings", json={"model": "m", "input": []})
    assert r2.status_code == 422


def test_healthz() -> None:
    """GET /healthz → 200 + 模型与维度信息正确（不加载模型）。"""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model"] == "BAAI/bge-small-zh-v1.5"
    assert body["dim"] == 512
