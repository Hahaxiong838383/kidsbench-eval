"""路由规则引擎契约测试：三档判定 + 配额闸 + 降级语义（评审定稿的行为锁死在这）。"""
from app.assistant_routing import (
    TIER_DIAGNOSIS,
    TIER_SIMPLE,
    TIER_UPGRADE,
    QuotaState,
    classify,
    decide_tier,
)

OK_QUOTA = QuotaState(quota_left=100000, upgrades_left=3, global_budget_ok=True)


def _user(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


# ---- 语义判档 ----

def test_manual_qa_is_simple():
    tier, _ = classify(_user("NoMemory 基线是干什么用的？"))
    assert tier == TIER_SIMPLE


def test_why_question_is_diagnosis():
    tier, reason = classify(_user("为什么 graphiti 分数这么低"))
    assert tier == TIER_DIAGNOSIS


def test_question_id_reference_is_diagnosis():
    tier, _ = classify(_user("第 42 题判分依据看一下"))
    assert tier == TIER_DIAGNOSIS


def test_qid_code_is_diagnosis():
    tier, _ = classify(_user("M0317 这道题的 gold 在哪轮"))
    assert tier == TIER_DIAGNOSIS


def test_system_name_with_question_is_diagnosis():
    tier, _ = classify(_user("reme 是怎么做检索的？"))
    assert tier == TIER_DIAGNOSIS


def test_long_question_failclosed_to_diagnosis():
    tier, reason = classify(_user("我想了解一下" + "评测的细节" * 60))
    assert tier == TIER_DIAGNOSIS
    assert "长问题" in reason


def test_session_stickiness():
    msgs = [
        {"role": "user", "content": "为什么 mem0 掉分"},
        {"role": "assistant", "content": "……", "tier": TIER_DIAGNOSIS},
        {"role": "user", "content": "那第二名呢"},
    ]
    tier, reason = classify(msgs)
    assert tier == TIER_DIAGNOSIS
    assert "粘性" in reason


# ---- 配额闸与降级语义 ----

def test_global_budget_exhausted_refuses_all():
    q = QuotaState(quota_left=100, upgrades_left=3, global_budget_ok=False)
    d = decide_tier(_user("NoMemory 是什么"), q)
    assert d.refused and "预算" in d.refuse_message


def test_phone_quota_exhausted_refuses():
    q = QuotaState(quota_left=0, upgrades_left=3, global_budget_ok=True)
    d = decide_tier(_user("NoMemory 是什么"), q)
    assert d.refused


def test_force_upgrade_honored():
    d = decide_tier(_user("随便一个问题"), OK_QUOTA, force_tier=TIER_UPGRADE)
    assert d.tier == TIER_UPGRADE and not d.refused


def test_force_upgrade_blocked_when_no_upgrades_left():
    q = QuotaState(quota_left=100000, upgrades_left=0, global_budget_ok=True)
    d = decide_tier(_user("随便一个问题"), q, force_tier=TIER_UPGRADE)
    assert d.refused and "升级次数" in d.refuse_message


def test_diagnosis_refuses_not_degrades_when_gateway_down():
    """评审最大 P0：诊断题上游挂 → 明确拒答，绝不降级弱模型硬答。"""
    d = decide_tier(_user("为什么 letta 第 3 题判错"), OK_QUOTA, upstream_gateway_ok=False)
    assert d.refused
    assert "不会降级" in d.refuse_message


def test_simple_unaffected_by_gateway_down():
    """简单档走国产，不依赖网关——网关挂了照常答。"""
    d = decide_tier(_user("判分三态是什么意思"), OK_QUOTA, upstream_gateway_ok=False)
    assert d.tier == TIER_SIMPLE and not d.refused


# ---- 定义豁免（唯一的降档规则，被性能词限制）----

def test_definitional_about_system_is_simple():
    """定义类问题即使提到系统名也是手册问答。"""
    tier, reason = classify(_user("letta 的 archival 是什么意思"))
    assert tier == TIER_SIMPLE


def test_definitional_with_performance_word_still_diagnosis():
    """定义措辞 + 性能词共现 → 问的是实例表现，豁免失效。"""
    tier, _ = classify(_user("mem0 掉分是什么原因"))
    assert tier == TIER_DIAGNOSIS


def test_definitional_with_qid_still_diagnosis():
    """实例引用优先级最高，定义豁免不适用。"""
    tier, _ = classify(_user("第 42 题的判分是什么意思"))
    assert tier == TIER_DIAGNOSIS
