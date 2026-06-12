"""AI 助手·chat SSE 端点 + 白盒自述。

链路：手机号 token 校验 → 配额闸 → 三档路由 → 流式 FC 对话 → 用量落账。
所有机制的"为什么"见 docs/ASSISTANT_PROPOSAL.md（V2.2，team 评审定稿）。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import assistant_db
from .assistant_auth import check_global_budget, check_phone_quota, verify_token
from .assistant_llm import builtin_endpoints, chat_stream
from .assistant_routing import (
    TIER_LABELS,
    TIER_UPGRADE,
    QuotaState,
    decide_tier,
)
from .assistant_tools import DOC_WHITELIST, execute_tool, tools_for_tier

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_KNOWLEDGE_PATH = Path(__file__).parent / "knowledge" / "KNOWLEDGE.md"

# 历史窗口上限（契约 §3.1：后端无状态，前端全量带历史，后端只留最近 N 条）
_HISTORY_LIMIT = 20
# 用户单条输入上限（评审 P0：输入 token 也要设闸）
_INPUT_CHAR_LIMIT = 4000


def _load_knowledge() -> tuple[str, str]:
    """返回 (手册全文, 生成时间)。手册是字节级稳定前缀——吃 prompt caching。"""
    if not _KNOWLEDGE_PATH.exists():
        return "（手册尚未生成）", "未生成"
    mtime = datetime.fromtimestamp(_KNOWLEDGE_PATH.stat().st_mtime).isoformat(timespec="seconds")
    return _KNOWLEDGE_PATH.read_text(encoding="utf-8"), mtime


def _system_prompt(tier: str) -> str:
    """system prompt 组装。缓存纪律：静态手册在最前且不掺动态变量，
    档位规则次之（每档字节级固定），动态变量（日期）放最末。"""
    knowledge, _ = _load_knowledge()
    tier_rules = {
        "simple": (
            "你是 KidsBench 评测平台的 AI 助手（简单档）。只基于上方手册和 read_doc"
            " 工具返回的资料回答；手册没覆盖的，提示用户「这个问题需要深度诊断，"
            "请点击回答下方的升级按钮」。绝不使用你的预训练知识臆测平台未验证的内容，"
            "超出范围要明确声明。回答必须标注依据来源（手册章节或文档名）。"
        ),
        "diagnosis": (
            "你是 KidsBench 评测平台的 AI 助手（诊断档）。你可以调用工具查榜单、"
            "题目、run 事务记录来诊断问题。结论必须基于工具返回的真实数据，"
            "每个结论标注数据来源。数据不足时如实说不足，绝不臆测。"
            "mem0/letta 等是知名开源项目，但你只能陈述本平台验证过的事实，"
            "平台未验证的特性必须声明「超出 KidsBench 验证范围」。"
        ),
    }
    tier_rules["upgrade"] = tier_rules["diagnosis"]
    return (
        f"{knowledge}\n\n---\n\n{tier_rules.get(tier, tier_rules['simple'])}"
        f"\n\n今天日期：{datetime.now().strftime('%Y-%m-%d')}"
    )


class ChatBody(BaseModel):
    messages: list[dict] = Field(min_length=1)
    force_tier: str | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _auth_phone(request: Request) -> str:
    # 公网外层 Basic Auth 占用 Authorization header，API token 走自定义头；
    # Bearer 仅本地 dev 兜底（2026-06-12 公网验证实战发现的 header 冲突）
    token = request.headers.get("X-Kidsbench-Token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
    payload = verify_token(token, kind="session") if token else None
    if payload is None:
        raise HTTPException(401, "会话无效或已过期，请重新输入手机号")
    return payload["phone"]


@router.post("/chat")
async def chat(body: ChatBody, request: Request) -> StreamingResponse:
    phone = _auth_phone(request)

    if assistant_db.get_setting("assistant_enabled") != "1":
        raise HTTPException(403, "助手已被管理员关闭")

    # 输入闸：单条用户输入限长（成本与注入面控制）
    last_user = next((m for m in reversed(body.messages) if m.get("role") == "user"), None)
    if last_user is None:
        raise HTTPException(422, "messages 里必须有 user 消息")
    if len(str(last_user.get("content", ""))) > _INPUT_CHAR_LIMIT:
        raise HTTPException(422, f"单条输入请控制在 {_INPUT_CHAR_LIMIT} 字以内")

    quota_raw = check_phone_quota(phone)
    quota = QuotaState(
        quota_left=quota_raw["quota_left"],
        upgrades_left=quota_raw["upgrades_left"],
        global_budget_ok=check_global_budget(),
    )
    decision = decide_tier(body.messages, quota, force_tier=body.force_tier)

    if decision.refused:
        # 拒答走非流式 403（前端渲染成提示条），不开 SSE
        raise HTTPException(403, decision.refuse_message)

    tier = decision.tier
    ep = builtin_endpoints()[tier]
    tools = tools_for_tier(tier)
    history = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in body.messages[-_HISTORY_LIMIT:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    messages = [{"role": "system", "content": _system_prompt(tier)}, *history]
    qhash = hashlib.sha1(str(last_user.get("content", "")).encode()).hexdigest()[:12]
    user_forced = 1 if body.force_tier == TIER_UPGRADE else 0

    async def event_gen():
        start = time.monotonic()
        yield _sse("meta", {
            "tier": tier,
            "tier_label": TIER_LABELS[tier],
            "model": ep.model,
            "routing_reason": decision.reason,  # 显性化：为什么进这个档
            "degraded": False,
        })
        tokens_in = tokens_out = 0
        degraded = False
        try:
            async for ev in chat_stream(
                ep, messages, tools,
                tool_executor=lambda name, args: execute_tool(tier, name, args),
            ):
                degraded = degraded or ev.degraded
                if ev.kind == "delta":
                    yield _sse("delta", {"text": ev.text})
                elif ev.kind == "tool_call":
                    for call in ev.tool_calls or []:
                        yield _sse("tool", {"name": call["function"]["name"], "status": "calling"})
                elif ev.kind == "error":
                    yield _sse("error", {"code": "UPSTREAM_DOWN", "message": ev.error})
                elif ev.kind == "done":
                    tokens_in, tokens_out = ev.tokens_in, ev.tokens_out
        finally:
            # 无论正常结束/客户端断开/上游错误都落账（finally 保证审计完整）
            assistant_db.log_usage(
                phone=phone, tier=tier, model=ep.model,
                tokens_in=tokens_in, tokens_out=tokens_out,
                latency_ms=(time.monotonic() - start) * 1000,
                user_forced=user_forced, degraded=1 if degraded else 0, qhash=qhash,
            )
        after = check_phone_quota(phone)
        yield _sse("done", {
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "quota_left": after["quota_left"], "upgrades_left": after["upgrades_left"],
            "degraded": degraded,
        })

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@router.get("/info")
def info() -> dict:
    """助手白盒自述（平台显性化纪律：机制讲人话，防误解）。"""
    _, manual_time = _load_knowledge()
    return {
        "what": "平台内置 AI 助手：解答 KidsBench 的设计思路、概念、榜单解读，并能诊断具体评测结果。",
        "knowledge_source": {
            "说明": "助手的知识 = 项目手册（构建思路与过程的精选汇编）+ 按需查阅的完整文档 + 实时数据工具。它被明确要求只基于这些回答，不得用预训练知识臆测。",
            "手册生成时间": manual_time,
            "可查文档": list(DOC_WHITELIST),
        },
        "tiers": {
            "简单档": "手册内问答，走国产轻量模型（deepseek），只配文档查阅工具。",
            "诊断档": "涉及『为什么/失败/对比/具体题目』的问题自动升档，走推理模型，可查榜单/题目/run 记录。",
            "强模型": "对回答不满意时手动点【用强模型重答】，走 gpt-5.5（每日次数有限）。",
        },
        "routing": "路由按规则判断并对每条回答显示档位角标与判档原因；拿不准时宁可升档（避免弱模型对诊断问题给出看似权威的错误结论）。",
        "degradation": "强模型网关故障时：事实问答自动降级（角标标黄），诊断类问题明确拒答而非降级硬答——错误的诊断比没有诊断更有害。",
        "boundaries": "助手只解答与诊断，不能修改代码、不能重跑评测、不能改题库。所有工具只读。",
        "quota": "按手机号配额管理；全局每日预算熔断。",
    }
