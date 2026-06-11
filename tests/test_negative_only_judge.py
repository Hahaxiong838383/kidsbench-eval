"""negative_only 判分单测（该遗忘型题，2026-06-11 新题库 S08 周报×3）。

题型语义：没有「该想起」只有「不该提」——周报不能翻出负面旧事
（吵架/留堂/父母离婚）。expected_facts 为空是设计意图。
判分规则：答案蕴含任一 negative → wrong；零命中 → correct。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.run_eval import _judge_answer
from kidsbench.middleware import NLIJudge


def _nli(script: dict):
    def fn(messages):
        user = messages[-1]["content"]
        for key, (label, conf) in script.items():
            if key in user:
                return json.dumps({"label": label, "confidence": conf})
        return json.dumps({"label": "neutral", "confidence": 0.9})
    return NLIJudge(model="m", base_url="u", api_key="k", completion_fn=fn)


Q_NEG_ONLY = {
    "qid": "S08-④-005",
    "judgment_mode": "negative_only",
    "expected_facts": [],
    "negative_facts": [
        {"hypothesis": "AI 在周报中提及了用户和朋友吵架的事",
         "polarity": "mutually_exclusive"},
    ],
}


def test_negative_only_clean_answer_correct():
    """没翻旧事 → correct。"""
    nli = _nli({"吵架": ("neutral", 0.9)})
    r = _judge_answer("这周任务完成 14 个，你的坚持小可看到了", Q_NEG_ONLY, nli)
    assert r["verdict"] == "correct" and r["score"] == 1.0
    assert r["negative_hits"] == []


def test_negative_only_mentions_negative_wrong():
    """周报里提了吵架 → wrong（雷区命中）。"""
    nli = _nli({"吵架": ("entailment", 0.9)})
    r = _judge_answer("这周你和朋友吵架了心情不好，但任务完成不错", Q_NEG_ONLY, nli)
    assert r["verdict"] == "wrong" and r["score"] == 0.0
    assert len(r["negative_hits"]) == 1


def test_standard_mode_unaffected():
    """standard 题不走 negative_only 分支（expected_facts 正常 NLI）。"""
    q = {
        "qid": "X", "judgment_mode": "standard",
        "expected_facts": [{"hypothesis": "AI 提到语文听写"}],
        "negative_facts": [],
    }
    nli = _nli({"语文听写": ("entailment", 0.9)})
    r = _judge_answer("今天有语文听写，加油", q, nli)
    assert r["verdict"] == "correct"
