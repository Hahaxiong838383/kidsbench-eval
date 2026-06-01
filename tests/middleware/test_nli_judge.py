"""NLI judge 测试（mock completion_fn，不真调 API）。"""
from __future__ import annotations

import json

from kidsbench.middleware import NLIJudge, judge_facts_nli


def _scripted(script: dict):
    """按 user content 含的关键词返回预设 label。"""
    def fn(messages):
        user = messages[-1]["content"]
        for key, (label, conf) in script.items():
            if key in user:
                return json.dumps({"label": label, "confidence": conf})
        return json.dumps({"label": "neutral", "confidence": 0.5})
    return fn


def _const(label: str, conf: float = 0.9):
    return lambda messages: json.dumps({"label": label, "confidence": conf})


def _judge(fn):
    return NLIJudge(model="m", base_url="u", api_key="k", completion_fn=fn)


def test_entail_label():
    assert _judge(_const("entailment")).entail("布偶猫", "养了布偶猫").is_entailment


def test_parse_bad_json_to_neutral():
    r = _judge(lambda m: "not json at all").entail("a", "b")
    assert r.label == "neutral" and r.confidence == 0.0


def test_confidence_clamped():
    r = _judge(_const("entailment", 1.5)).entail("a", "b")
    assert r.confidence == 1.0


def test_judge_correct():
    """positive 蕴含 + 无乱猜 → correct。"""
    nli = _judge(_const("entailment", 0.95))
    r = judge_facts_nli("我养的是布偶猫", [{"hypothesis": "养了布偶猫"}], [], nli)
    assert r["verdict"] == "correct" and r["score"] == 1.0


def test_judge_guessed_wrong():
    """答案蕴含互斥 negative → wrong（乱猜）。"""
    nli = _judge(_scripted({"霸王龙": ("entailment", 0.9), "三角龙": ("neutral", 0.8)}))
    r = judge_facts_nli(
        "你最喜欢霸王龙",
        [{"hypothesis": "最喜欢三角龙"}],
        [{"hypothesis": "最喜欢霸王龙", "polarity": "mutually_exclusive"}],
        nli,
    )
    assert r["verdict"] == "wrong" and r["guessed"] is True


def test_judge_evasive():
    """positive 不蕴含 + 无乱猜 → evasive。"""
    nli = _judge(_const("neutral", 0.9))
    r = judge_facts_nli("我不知道", [{"hypothesis": "养了布偶猫"}], [], nli)
    assert r["verdict"] == "evasive"


def test_need_human_low_confidence():
    """confidence<0.7 → need_human=True（不影响 label 判定）。"""
    nli = _judge(_const("entailment", 0.5))
    r = judge_facts_nli("布偶猫", [{"hypothesis": "养了布偶猫"}], [], nli)
    assert r["verdict"] == "correct" and r["need_human"] is True
