"""AI 助手·只读工具层（按档位裁剪是安全边界的一部分）。

设计依据（team 评审共识）：
- 简单档物理隔离诊断工具：schema 里只给 read_doc，弱模型连"瞎调诊断工具"的
  机会都没有。诊断工具是诊断/升级档专属。
- read_doc 用白名单字典（语义名→硬编码路径），不接受任意路径——
  防 prompt injection 骗模型传 ../../.env.local（评审 P1：路径穿越）。
- 白名单排除了历史执行 prompt 类文档（QUESTIONBANK_EXECUTION_PROMPT 等），
  它们本身是指令文本，是文档内注入的风险源。
- get_run_log 的路径 resolve 后必须落在 runs 目录内（双保险）。
"""
from __future__ import annotations

import json

# ---- read_doc 白名单：语义名 → 仓库内相对路径 ----
# 本地布局 repo/web/backend/app/ → parents[3]=repo；容器扁平化为 /app/app/
# → 用 env 显式指定（Dockerfile 设 KIDSBENCH_REPO_ROOT=/app）
import os
from pathlib import Path

from .config import RUNS_PATH

def _detect_repo_root() -> Path:
    """env 优先；本地布局回退 parents[3]。注意不能写成 environ.get(k, default)
    ——default 表达式会被急切求值，容器扁平布局下 parents[3] 直接 IndexError
    （部署实战炸过，2026-06-12）。"""
    env_root = os.environ.get("KIDSBENCH_REPO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[3]


_REPO_ROOT = _detect_repo_root()
DOC_WHITELIST: dict[str, str] = {
    "评测范式研究": "docs/EVAL_PARADIGM_RESEARCH.md",
    "harness接口规范": "docs/HARNESS_INTERFACE_SPEC.md",
    "题库转换管线": "docs/CONVERSION_PIPELINE.md",
    "题库修复说明": "docs/QUESTIONBANK_V01_FIX_NOTES.md",
    "能力矩阵": "docs/CAPABILITY_MATRIX.md",
    "新系统接入清单": "docs/NEW_SYSTEM_CHECKLIST.md",
    "reme核实事实": "docs/REME_VERIFIED_FACTS.md",
    "letta核实事实": "docs/LETTA_VERIFIED_FACTS.md",
    "hindsight核实事实": "docs/HINDSIGHT_VERIFIED_FACTS.md",
    "web平台规范": "docs/WEB_PLATFORM_SPEC.md",
    "助手方案": "docs/ASSISTANT_PROPOSAL.md",
}

_DOC_CHAR_LIMIT = 24000  # 单文档注入上限，防一次塞爆上下文


def _tool_read_doc(args: dict) -> str:
    name = str(args.get("name", ""))
    rel = DOC_WHITELIST.get(name)
    if rel is None:
        return "未找到该文档。可用文档名：" + "、".join(DOC_WHITELIST)
    path = (_REPO_ROOT / rel).resolve()
    if not path.is_relative_to(_REPO_ROOT) or not path.exists():
        return "文档不可用。"
    text = path.read_text(encoding="utf-8")
    if len(text) > _DOC_CHAR_LIMIT:
        text = text[:_DOC_CHAR_LIMIT] + "\n…（文档过长已截断）"
    return f"《{name}》（{rel}）：\n{text}"


def _tool_get_leaderboard(args: dict) -> str:
    # 复用题库板块的榜单端点函数（与 web 显示同源，天然一致）
    from .questionbank import leaderboard

    data = dict(leaderboard())
    data.pop("findings", None)  # 榜单和发现拆成两个工具，按需取省 token
    return json.dumps(data, ensure_ascii=False, default=str)[:8000]


def _tool_get_findings(args: dict) -> str:
    from .questionbank import leaderboard

    data = leaderboard()
    return json.dumps(data.get("findings", []), ensure_ascii=False, default=str)[:8000]


def _tool_get_question(args: dict) -> str:
    from fastapi import HTTPException

    from .questionbank import question_detail

    qid = str(args.get("qid", ""))
    try:
        return json.dumps(question_detail(qid), ensure_ascii=False)[:6000]
    except HTTPException:
        return f"未找到题目 {qid}。"


def _tool_get_run_log(args: dict) -> str:
    """读某次 run 里某题的事务记录。路径双重校验防穿越。"""
    run_id = str(args.get("run_id", "")).strip()
    qid = str(args.get("qid", "")).strip()
    runs_root = Path(RUNS_PATH).resolve()
    if not run_id or not qid:
        runs = sorted(p.name for p in runs_root.glob("run_*"))[-10:] if runs_root.exists() else []
        return "需要 run_id 和 qid 两个参数。最近的 run：" + "、".join(runs)
    run_dir = (runs_root / run_id).resolve()
    if not run_dir.is_relative_to(runs_root) or not run_dir.is_dir():
        return f"未找到 run {run_id}。"
    hits: list[str] = []
    for jf in sorted(run_dir.glob("*.jsonl")):
        for line in jf.read_text(encoding="utf-8").splitlines():
            if qid in line:
                hits.append(line)
            if len(hits) >= 20:
                break
        if len(hits) >= 20:
            break
    if not hits:
        return f"run {run_id} 中未找到题目 {qid} 的记录。"
    return "\n".join(hits)[:10000]


# ---- 档位 → 工具 schema（契约 §6）----

_READ_DOC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_doc",
        "description": "按文档名读取平台完整文档（手册没讲透的细节用它查）。可用文档名："
        + "、".join(DOC_WHITELIST),
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "文档名（白名单内）"}},
            "required": ["name"],
        },
    },
}

_DIAGNOSIS_SCHEMAS = [
    _READ_DOC_SCHEMA,
    {"type": "function", "function": {
        "name": "get_leaderboard", "description": "获取当前评测总榜（各记忆系统得分排名）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_findings", "description": "获取榜单自动发现的结论（区分度/异常信号等）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_question", "description": "按题号获取题目详情（场景/触发输入/判分命题）",
        "parameters": {"type": "object", "properties": {
            "qid": {"type": "string", "description": "题目 ID"}}, "required": ["qid"]}}},
    {"type": "function", "function": {
        "name": "get_run_log",
        "description": "读取某次评测 run 中某题的完整事务记录（prompt/回答/判分），诊断判错原因用",
        "parameters": {"type": "object", "properties": {
            "run_id": {"type": "string", "description": "run 目录名，如 run_1781238966"},
            "qid": {"type": "string", "description": "题目 ID"}},
            "required": ["run_id", "qid"]}}},
]

_EXECUTORS = {
    "read_doc": _tool_read_doc,
    "get_leaderboard": _tool_get_leaderboard,
    "get_findings": _tool_get_findings,
    "get_question": _tool_get_question,
    "get_run_log": _tool_get_run_log,
}

_TIER_TOOLS = {
    "simple": [_READ_DOC_SCHEMA],  # 物理隔离：简单档只有 read_doc
    "diagnosis": _DIAGNOSIS_SCHEMAS,
    "upgrade": _DIAGNOSIS_SCHEMAS,
}


def tools_for_tier(tier: str) -> list[dict]:
    return _TIER_TOOLS.get(tier, [])


def execute_tool(tier: str, name: str, args: dict) -> str:
    """执行工具。档位裁剪在执行层再查一遍（schema 层防不住注入伪造的调用名）。"""
    allowed = {t["function"]["name"] for t in tools_for_tier(tier)}
    if name not in allowed:
        return f"工具 {name} 在当前档位不可用。"
    fn = _EXECUTORS.get(name)
    if fn is None:
        return f"未知工具 {name}。"
    try:
        return fn(args)
    except Exception as exc:  # 工具失败要让模型知道并继续，不能炸整个流
        return f"工具执行失败：{type(exc).__name__}: {exc}"
