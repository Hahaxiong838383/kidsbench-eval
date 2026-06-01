"""判分器：先正则硬匹配，可叠加 LLM-as-judge。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JudgeResult:
    """单题判分结果。"""

    score: float       # [0, 1]，1=完全对，0=完全错
    positive_hits: list[str]   # 命中的 expected points
    negative_hits: list[str]   # 命中的 negative points（凭常识乱猜）
    verdict: str       # 'correct' / 'partial' / 'wrong' / 'evasive'


def regex_judge(answer: str, question: dict) -> JudgeResult:
    """正则硬匹配判分（确定性，零 LLM 成本）。

    评分规则：
    - 含任一 expected_answer_points → 得 1.0
    - 不含 expected 但含 negative_answer_points → 得 0.0（凭常识乱猜）
    - 都不含 → 得 0.0（拒答/答非所问）
    """
    if not answer:
        return JudgeResult(score=0.0, positive_hits=[], negative_hits=[], verdict="evasive")

    answer_lower = answer.lower()
    expected = question.get("expected_answer_points", [])
    negative = question.get("negative_answer_points", [])

    positive_hits = [p for p in expected if p and p.lower() in answer_lower]
    negative_hits = [n for n in negative if n and n.lower() in answer_lower]

    if positive_hits:
        # 命中 expected 即算对，即使同时含 negative（LLM 可能列举）
        return JudgeResult(
            score=1.0,
            positive_hits=positive_hits,
            negative_hits=negative_hits,
            verdict="correct",
        )
    if negative_hits:
        return JudgeResult(
            score=0.0,
            positive_hits=[],
            negative_hits=negative_hits,
            verdict="wrong",  # 凭常识答错（最危险的失败模式）
        )
    return JudgeResult(
        score=0.0,
        positive_hits=[],
        negative_hits=[],
        verdict="evasive",  # 没说错也没说对
    )


def recall_score(recalled_turn_ids: list[str], gold_memory_ids: list[str]) -> dict:
    """召回率指标（独立于答案）。

    召回率本身就是评测核心维度 ④ 记忆召回。
    """
    if not gold_memory_ids:
        return {"recall": None, "precision": None, "hit_count": 0}
    recalled = set(recalled_turn_ids)
    gold = set(gold_memory_ids)
    hits = recalled & gold
    return {
        "recall": len(hits) / len(gold) if gold else 0.0,
        "precision": len(hits) / len(recalled) if recalled else 0.0,
        "hit_count": len(hits),
        "missed": sorted(gold - recalled),
        "extra": sorted(recalled - gold),
    }


def attribution_f1(
    recalled_turn_ids: list[str],
    gold_turn_ids: list[str],
    fact_distribution: str = "single",
) -> dict:
    """Turn-level Attribution F1（C 决策核心，免打折惩罚全量兜底）。

    - Precision 两口径都严格：全量兜底 T_pred 越大，precision 越低 → F1 暴跌。
    - single：标准 Recall = |命中| / |gold|。
    - distributed：同一事实多 turn 重复，Recall = 1.0 if 命中≥1 else 0.0（覆盖语义）。
    - T_pred 为空（系统不支持 traceback）→ counted=False，该题 attribution 不计入。

    返回新 dict（不可变）。
    """
    t_pred = set(recalled_turn_ids)
    t_gold = set(gold_turn_ids)
    if not t_pred or not t_gold:
        return {"f1": None, "precision": None, "recall": None, "counted": False, "hit": 0}

    hit = len(t_pred & t_gold)
    precision = hit / len(t_pred)
    if fact_distribution == "distributed":
        recall = 1.0 if hit >= 1 else 0.0
    else:
        recall = hit / len(t_gold)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "f1": f1,
        "precision": precision,
        "recall": recall,
        "counted": True,
        "hit": hit,
        "fact_distribution": fact_distribution,
    }
