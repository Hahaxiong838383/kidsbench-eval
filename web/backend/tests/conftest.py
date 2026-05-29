"""pytest fixtures。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_runs(tmp_path: Path, monkeypatch) -> Path:
    """构造临时 runs/ 目录 + 2 个示例 group。

    结构：
        runs/
        ├── mem0_bge/
        │   ├── results.jsonl
        │   └── summary.json
        └── memoryos_bge/
            ├── results.jsonl
            └── summary.json
    """
    runs = tmp_path / "runs"
    runs.mkdir()

    # mem0_bge
    g1 = runs / "mem0_bge"
    g1.mkdir()
    rows_mem0 = [
        {
            "qid": "q_001",
            "adapter": "mem0",
            "user_id": "eval_mem0_q_001",
            "success": True,
            "answer": "团子是布偶猫",
            "judge_verdict": "correct",
            "judge_score": 1.0,
            "timestamp": 1700000010.0,
            "recalled_count": 3,
        },
        {
            "qid": "q_001",
            "adapter": "nomemory",
            "user_id": "eval_nomemory_q_001",
            "success": True,
            "answer": "不知道",
            "judge_verdict": "evasive",
            "judge_score": 0.0,
            "timestamp": 1700000005.0,
            "recalled_count": 0,
        },
    ]
    (g1 / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows_mem0),
        encoding="utf-8",
    )
    (g1 / "summary.json").write_text(
        json.dumps(
            {
                "nomemory": {"correct": 0, "wrong": 0, "evasive": 6, "error": 0, "total": 6},
                "mem0": {"correct": 6, "wrong": 0, "evasive": 0, "error": 0, "total": 6},
            }
        ),
        encoding="utf-8",
    )

    # memoryos_bge
    g2 = runs / "memoryos_bge"
    g2.mkdir()
    rows_memoryos = [
        {
            "qid": "q_001",
            "adapter": "memoryos",
            "user_id": "eval_memoryos_q_001",
            "success": True,
            "answer": "团子是布偶猫",
            "judge_verdict": "correct",
            "judge_score": 1.0,
            "timestamp": 1700000020.0,
            "recalled_count": 2,
        },
    ]
    (g2 / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows_memoryos),
        encoding="utf-8",
    )
    (g2 / "summary.json").write_text(
        json.dumps(
            {
                "memoryos": {"correct": 6, "wrong": 0, "evasive": 0, "error": 0, "total": 6},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("KIDSBENCH_RUNS_PATH", str(runs))
    # 强制 reload config（避免模块缓存）
    import importlib

    from app import config as _config

    importlib.reload(_config)
    from app import runs as _runs
    from app import state as _state

    importlib.reload(_runs)
    importlib.reload(_state)

    return runs


@pytest.fixture
def client(tmp_runs) -> TestClient:
    """启动 FastAPI test client（依赖 tmp_runs 完成 config reload）。"""
    import importlib

    from app import main as _main

    importlib.reload(_main)
    return TestClient(_main.app)
