"""Attribution F1 测试（C 决策：免打折惩罚全量兜底 + 单/分布式双口径）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.scorer import attribution_f1


def test_single_precise():
    """精确系统：pred={t3} gold={t3} → F1=1.0。"""
    r = attribution_f1(["t3"], ["t3"], "single")
    assert r["counted"] and r["f1"] == 1.0


def test_full_fallback_punished():
    """全量兜底：pred=50 turns, gold={t3} → precision=0.02, F1≈0.039。"""
    pred = [f"t{i}" for i in range(50)]
    r = attribution_f1(pred, ["t3"], "single")
    assert r["precision"] == 1 / 50
    assert r["recall"] == 1.0
    assert abs(r["f1"] - 0.0392) < 0.001   # gemini 模拟值 ≈0.039


def test_distributed_recall_relaxed():
    """分布式：召回 1 个就算覆盖，pred={t1} gold={t1..t10} → recall=1.0 F1=1.0。"""
    gold = [f"t{i}" for i in range(10)]
    r = attribution_f1(["t0"], gold, "distributed")
    assert r["recall"] == 1.0 and r["f1"] == 1.0


def test_distributed_precision_still_strict():
    """分布式 Precision 仍严格：全量兜底照样被惩罚。"""
    pred = [f"t{i}" for i in range(50)]
    gold = [f"t{i}" for i in range(10)]
    r = attribution_f1(pred, gold, "distributed")
    assert r["precision"] == 10 / 50   # 0.2，惩罚兜底
    assert r["recall"] == 1.0


def test_single_distributed_differ():
    """同样召回，single 比 distributed 的 recall 低（验证双口径生效）。"""
    gold = [f"t{i}" for i in range(10)]
    single = attribution_f1(["t0"], gold, "single")
    dist = attribution_f1(["t0"], gold, "distributed")
    assert single["recall"] == 0.1 and dist["recall"] == 1.0


def test_empty_pred_not_counted():
    """系统不支持 traceback（T_pred 空）→ 不计入。"""
    r = attribution_f1([], ["t3"], "single")
    assert r["counted"] is False and r["f1"] is None


def test_empty_gold_not_counted():
    r = attribution_f1(["t1"], [], "single")
    assert r["counted"] is False
