"""断点续跑 _load_resume_state 测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.run_eval import _load_resume_state


def test_rebuild_done_and_summary(tmp_path):
    f = tmp_path / "results.jsonl"
    f.write_text(
        "\n".join([
            json.dumps({"adapter": "mem0", "qid": "q1", "success": True, "judge_verdict": "correct"}),
            json.dumps({"adapter": "mem0", "qid": "q2", "success": True, "judge_verdict": "wrong"}),
            json.dumps({"adapter": "mem0", "qid": "q3", "success": True, "judge_verdict": "evasive"}),
            json.dumps({"adapter": "mem0", "qid": "q4", "success": False, "judge_verdict": "error"}),
        ]),
        encoding="utf-8",
    )
    done, summary = _load_resume_state(f)
    assert ("mem0", "q1") in done and ("mem0", "q4") in done
    s = summary["mem0"]
    assert s == {"correct": 1, "wrong": 1, "evasive": 1, "error": 1, "total": 4}


def test_corrupt_line_skipped(tmp_path):
    """中断写一半的损坏 JSON 行 → 跳过，不误判已完成、不崩。"""
    f = tmp_path / "results.jsonl"
    f.write_text(
        json.dumps({"adapter": "mem0", "qid": "q1", "success": True, "judge_verdict": "correct"})
        + "\n"
        + '{"adapter":"mem0","qid":"q2",  # 写一半损坏\n',
        encoding="utf-8",
    )
    done, summary = _load_resume_state(f)
    assert ("mem0", "q1") in done
    assert ("mem0", "q2") not in done  # 损坏行不算完成 → 会被重跑
    assert summary["mem0"]["total"] == 1


def test_empty_file(tmp_path):
    f = tmp_path / "results.jsonl"
    f.write_text("", encoding="utf-8")
    done, summary = _load_resume_state(f)
    assert done == set() and summary == {}
