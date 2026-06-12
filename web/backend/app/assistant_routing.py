"""AI 助手·三档路由规则引擎（纯函数，零 IO，可单测）。

设计依据（ASSISTANT_PROPOSAL.md V2.2，team 三发评审定稿）：
- 砍掉独立 LLM 分类器——误判诊断题让弱模型瞎答的代价远大于多烧配额，
  所以规则激进化 + fail-closed（拿不准一律上档）。
- 「需要诊断工具」本身是最干净的路由信号：含题号 / run / 日志 / 系统名+负面词
  的问题没有诊断工具答不了，直接上诊断档。
- 降级语义分级（三家评审最大 P0 共识）：上游挂了时事实问答可降级，
  诊断题必须明确拒答——降级弱模型硬答诊断题=权威胡说，比拒答恶劣。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---- 档位常量（与 settings 表 tier_* 键和前端角标对应）----
TIER_SIMPLE = "simple"
TIER_DIAGNOSIS = "diagnosis"
TIER_UPGRADE = "upgrade"

TIER_LABELS = {
    TIER_SIMPLE: "简单档",
    TIER_DIAGNOSIS: "诊断档",
    TIER_UPGRADE: "强模型",
}

# ---- 诊断语义信号（任一命中即上诊断档，fail-closed 宁可错升）----

# 定义/概念类措辞：手册问答的典型形态。它优先于诊断信号——
# "NoMemory 基线是干什么用的？"这类问题让 thinking 模型答是浪费延迟。
# 但只在不含性能/失败词时生效（"mem0 为什么判错"含"判错"仍上诊断档）。
_DEFINITIONAL = re.compile(
    r"是什么|是干什么|干什么用|干嘛用|什么意思|啥意思|怎么理解|介绍一下|解释一下|科普"
)

# 因果/诊断/对比类措辞：这些问题需要查数据+推理，弱模型容易"权威胡说"
# （金标集 dry-run 补充了两组盲区：综合类措辞——"请总结/如何影响"是跨文档
# 综合，简单档答会以偏概全；排障类措辞——部署/报错问题需要查核实文档）
_DIAGNOSIS_WORDS = re.compile(
    r"为什么|为何|怎么回事|诊断|失败|判错|错了|没过|没通过|挂了"
    r"|对比|比较|差异|区别在哪|原因|分析|排查|根因|怎么差|低这么多|掉分"
    r"|总结|简述|梳理|盘点|汇总|如何影响|演进|时间线|发生.{0,4}变化"
    r"|部署|安装|报错|找不到|连不上|起不来|配置.{0,4}(错|失败|问题)"
)

# 豁免拦截词：与定义类措辞共现时说明并非纯概念问题，定义豁免失效——
# 性能表现（问实例）/ 排障（要查核实文档）/ 综合（跨文档，简单档会以偏概全）
_PERFORMANCE_WORDS = re.compile(
    r"分数|得分|排名|榜|掉|失败|判错|错了|没过|挂了|慢|耗时|成本"
    r"|部署|安装|报错|找不到|连不上|总结|简述|梳理|盘点"
)

# 题号/run/日志指称：提到具体评测对象=必然要查 run 记录
_ENTITY_REF = re.compile(
    r"第\s*\d+\s*题|[A-Z]\d{2,}|qid|run[_\s-]?\w*|日志|log|事务记录", re.IGNORECASE
)

# 被测系统名 + 负面/疑问共现（"graphiti 怎么…"这类一定是诊断）
_SYSTEM_NAMES = re.compile(
    r"mem0|memoryos|graphiti|hindsight|reme|letta|nomemory|fullhistory|oracle",
    re.IGNORECASE,
)

# 长问题（>300 字）大概率是多步综合，fail-closed 上档
_LONG_QUESTION_CHARS = 300


@dataclass(frozen=True)
class QuotaState:
    """路由需要的配额快照（由 assistant_auth.check_phone_quota 提供）。"""

    quota_left: int  # 该手机号今日剩余 token
    upgrades_left: int  # 该手机号今日剩余手动升级次数
    global_budget_ok: bool  # 全局每日预算是否未耗尽


@dataclass(frozen=True)
class RoutingDecision:
    """路由结果。refused=True 时 tier 无意义，refuse_message 给前端人话提示。"""

    tier: str
    reason: str  # 命中了哪条规则（显性化：日志和 /info 都用它解释路由）
    refused: bool = False
    refuse_message: str = ""


def _latest_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def _previous_tier_was_diagnosis(messages: list[dict]) -> bool:
    """会话粘性：上一轮是诊断对话时，追问（"那第二名呢"）大概率延续诊断语境。"""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("tier") == TIER_DIAGNOSIS
    return False


def classify(messages: list[dict]) -> tuple[str, str]:
    """纯语义判档（不看配额）。返回 (tier, 命中原因)。

    优先级：实例引用（题号/run）> 定义豁免 > 诊断措辞 > fail-closed 兜底。
    误判方向上的取舍：宁可把概念题误升诊断档（多花点便宜的国产推理token），
    绝不把诊断题误降简单档（弱模型权威胡说）。定义豁免是唯一的"降档"规则，
    且被性能词共现条款限制住。
    """
    text = _latest_user_text(messages)
    # 实例引用最优先：提到具体题号/run 一定要查数据，定义豁免不适用
    if _ENTITY_REF.search(text):
        return TIER_DIAGNOSIS, "提到题号/run/日志等具体评测对象"
    # 定义豁免：概念解释类是手册问答，且不含性能词才豁免
    if _DEFINITIONAL.search(text) and not _PERFORMANCE_WORDS.search(text):
        return TIER_SIMPLE, "定义/概念类问题，手册可答"
    if _DIAGNOSIS_WORDS.search(text):
        return TIER_DIAGNOSIS, "命中诊断措辞（为什么/失败/对比类）"
    if _SYSTEM_NAMES.search(text) and ("?" in text or "？" in text or "吗" in text or "怎" in text):
        return TIER_DIAGNOSIS, "对具体记忆系统提出疑问"
    if len(text) > _LONG_QUESTION_CHARS:
        return TIER_DIAGNOSIS, f"长问题（>{_LONG_QUESTION_CHARS} 字），按多步综合处理"
    if _previous_tier_was_diagnosis(messages):
        return TIER_DIAGNOSIS, "会话粘性：延续上一轮诊断语境"
    return TIER_SIMPLE, "未命中诊断信号，手册问答"


def decide_tier(
    messages: list[dict],
    quota: QuotaState,
    force_tier: str | None = None,
    upstream_gateway_ok: bool = True,
) -> RoutingDecision:
    """完整路由：语义判档 → 配额闸 → 降级语义。

    降级铁律：诊断/升级档上游不可用时**明确拒答**，绝不降级到弱模型硬答。
    """
    # 0) 全局预算闸（最先拦，省一切后续判断）
    if not quota.global_budget_ok:
        return RoutingDecision(
            tier=TIER_SIMPLE, reason="全局预算耗尽", refused=True,
            refuse_message="今日助手总用量已达预算上限，明天再来吧。",
        )
    if quota.quota_left <= 0:
        return RoutingDecision(
            tier=TIER_SIMPLE, reason="个人配额耗尽", refused=True,
            refuse_message="你今日的助手配额已用完，可联系管理员调整。",
        )

    # 1) 手动升级（用户显式点按钮，最高优先级）
    if force_tier == TIER_UPGRADE:
        if quota.upgrades_left <= 0:
            return RoutingDecision(
                tier=TIER_UPGRADE, reason="升级次数耗尽", refused=True,
                refuse_message="今日强模型升级次数已用完（防止配额被打光），明天恢复。",
            )
        if not upstream_gateway_ok:
            return RoutingDecision(
                tier=TIER_UPGRADE, reason="网关不可用", refused=True,
                refuse_message="强模型网关暂不可用，请稍后再试。",
            )
        return RoutingDecision(tier=TIER_UPGRADE, reason="用户手动升级")

    # 2) 语义判档
    tier, reason = classify(messages)

    # 3) 诊断档的上游闸：诊断档默认走国产（不依赖网关），但若 settings 把
    #    诊断档切到了网关模型，调用方会把 upstream_gateway_ok 传进来
    if tier == TIER_DIAGNOSIS and not upstream_gateway_ok:
        return RoutingDecision(
            tier=tier, reason=reason, refused=True,
            refuse_message=(
                "诊断引擎暂不可用。为避免给出不可靠的诊断结论，"
                "这类问题不会降级到弱模型回答，请稍后再试。"
            ),
        )

    return RoutingDecision(tier=tier, reason=reason)
