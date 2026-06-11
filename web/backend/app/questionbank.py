"""题库板块 API：总览 / 管线白话说明 / 修理记录 / 题目详情 / CSV 上传转换。

设计原则（川哥要求，2026-06-11）：
- harness 的作用、转换管线每一步、修复补丁的依据和逻辑，全部显性化，
  术语配白话——给后续上传 CSV 题库的人看得懂
- 上传只跑转换+门禁（秒级、纯解析、不烧 token），评测跑批仍手动触发
- 上传产物存 data/uploads/（带时间戳），不覆盖正式题库
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from .config import PROJECT_ROOT, QUESTIONS_PATH

# converter 在 src/kidsbench 包内（容器 PYTHONPATH=/app/src 已含）
_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

router = APIRouter(prefix="/api/questionbank", tags=["questionbank"])

UPLOADS_PATH = Path(
    str(PROJECT_ROOT / "data" / "uploads")
)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB：197 题 CSV 实测 ~350KB，5MB 足够余量

# ---------------------------------------------------------------- 静态说明文案
# 全部「术语 + 白话」，是 web 显性化的内容源。改机制时必须同步改这里。

PIPELINE_STEPS = [
    {
        "step": 1,
        "term": "CSV 版本快照",
        "plain": "从飞书表下载的 CSV 原样存档，永不修改。出问题随时能对回原始数据。",
        "detail": "存放于 questions/raw/，文件名带日期。所有修复都不动这份文件。",
    },
    {
        "step": 2,
        "term": "补丁层（patches）",
        "plain": "发现题目有问题不直接改表，而是写一张『修理单』：问题是什么、为什么是问题、怎么修。转换时自动套用。",
        "detail": "每个补丁含 problem/diagnosis/fix 三段说明 + 机器操作（ops）。"
                  "新 CSV 上传后补丁自动重放；若题目在源头已被修改，补丁会显式报"
                  " patch_failed——这不是错误，是『源头已改，请复核后删除该补丁』的信号。",
    },
    {
        "step": 3,
        "term": "解析（自由文本 → 结构化对话）",
        "plain": "把『[T-1d 16:30] 小可: \"…\"』这样的文字拆成一条条带时间、带说话人的标准记录。",
        "detail": "支持 4 种时间格式变体 + 系统事件行 + 元注释。T±Nd 转成真实时间戳"
                  "（固定基准日 2026-06-01，可复现）。",
    },
    {
        "step": 4,
        "term": "协议切分（v1.1）",
        "plain": "判断每段对话该去哪：昨天和更早的对话 → 写进记忆系统（考记忆）；今天当场的对话 → 直接放进 AI 的眼前上下文（产品本来就看得见，不考记忆）。",
        "detail": "依据 2026-06-11 产品事实裁决：小可 runtime 当场对话在 LLM context 里。"
                  "会话边界 = '---' 分隔符或日期变化。判断口诀：『这段对话在触发发生时"
                  "还在屏幕上吗？』在 → 当前上下文；不在 → 写记忆。",
    },
    {
        "step": 5,
        "term": "gold 锚定（该想起哪句 → 具体记录）",
        "plain": "把『该想起哪句』精确对到对话里的某一条记录。对不上的题进修题清单——因为判分要数『AI 想起的是不是正确的那句』，对不上就没法判。",
        "detail": "匹配分三级防误判：≥4 字前缀匹配 / 恰 3 字必须整句相等 / 单字（好、嗯）"
                  "拒绝——单字确认句没有归因力，该把 gold 挂到信息承载句上。"
                  "多句 gold 用 // 分隔逐条锚定。",
    },
    {
        "step": 6,
        "term": "红线门禁",
        "plain": "两道自动检查：①场景信息里不能藏答案（否则没有记忆的系统也能答对，评测就废了）②记忆题的答案必须在历史对话里（不能在当场对话里）。",
        "detail": "命中即拦截进修题清单，绝不静默放行。",
    },
    {
        "step": 7,
        "term": "判分命题合并（hypotheses）",
        "plain": "把人话的『答对要点』翻译成机器能判的完整句子。事实类（提没提到语文听写）走 NLI 自动判分；风格类（语气温不温和）机器判不了，留给评分锚点走人工/裁判模型。",
        "detail": "LLM 产草稿 + 规则校验 + 人工审核，状态（draft/approved）随题可查。",
    },
]

HARNESS_EXPLAINED = {
    "term": "Harness（评测执行器）",
    "plain": "考场监考官。它拿着转换好的题目，对每个记忆系统做同一套动作：清场 → 把历史对话一条条喂给记忆系统 → 等它消化 → 用触发输入提问 → 收集回答 → 判分。全程对每家系统一视同仁，差异只来自记忆系统本身。",
    "steps": [
        {"term": "clear（清场）", "plain": "每道题开始前把记忆库清空，防止上一题的记忆残留影响这一题。"},
        {"term": "write（逐条写入）", "plain": "把历史对话一条条喂给记忆系统——就像孩子和小可真实聊过这些天。"},
        {"term": "flush + consolidate（固化）", "plain": "等记忆系统把刚收到的内容消化完（建索引、整理），再开考。"},
        {"term": "read（检索）", "plain": "用触发输入去记忆系统里查：它能想起哪些相关内容？"},
        {"term": "prompt 组装", "plain": "把『当前场景 + 记忆系统想起的内容 + 当场对话 + 触发输入』拼给回答模型——当场对话直接给（产品里本来就看得见），历史只能靠记忆系统想起来。"},
        {"term": "判分", "plain": "两条线：①召回判分——想起的是不是正确的那句（Attribution）；②回答判分——事实对不对走 NLI 自动判，风格好不好走 0-3 锚点。"},
    ],
    "baselines": {
        "term": "基线（NoMemory / FullHistory）",
        "plain": "两个参照物：NoMemory 完全没有记忆（考不及格才正常——如果它都能答对，说明题目泄了答案）；FullHistory 把全部历史原文塞给模型（理论上限——记忆系统越接近它越好，但它贵且不现实）。",
    },
}


# ---------------------------------------------------------------- 数据加载

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _bank_paths() -> dict[str, Path]:
    return {
        "jsonl": QUESTIONS_PATH / "v01_memory.jsonl",
        "patches": QUESTIONS_PATH / "patches" / "v01_memory_patches.json",
        "hypotheses": QUESTIONS_PATH / "patches" / "v01_memory_hypotheses.json",
        "issues": QUESTIONS_PATH / "v01_memory_issues.csv",
        "raw_dir": QUESTIONS_PATH / "raw",
    }


# ---------------------------------------------------------------- API

@router.get("")
def overview() -> dict:
    """题库总览：版本、健康度、管线白话说明、harness 白话说明。"""
    paths = _bank_paths()
    questions = _load_jsonl(paths["jsonl"])
    patches = _load_json(paths["patches"])
    raw_files = sorted(p.name for p in paths["raw_dir"].glob("*.csv")) \
        if paths["raw_dir"].exists() else []

    from collections import Counter
    task_dist = Counter(q.get("task_type", "?") for q in questions)
    cat_dist = Counter(p.get("category", "?") for p in patches)

    return {
        "bank_version": "v0.1_记忆",
        "raw_snapshots": raw_files,
        "health": {
            "total_in_jsonl": len(questions),
            "patched": sum(1 for q in questions if q.get("patched")),
            "with_current_session": sum(1 for q in questions if q.get("current_session")),
            "negative_only": sum(1 for q in questions
                                 if q.get("judgment_mode") == "negative_only"),
            "task_distribution": dict(task_dist),
        },
        "fixes_summary": {
            "total_patches": len(patches),
            "by_category": dict(cat_dist),
        },
        "pipeline": PIPELINE_STEPS,
        "harness": HARNESS_EXPLAINED,
    }


@router.get("/fixes")
def fixes() -> dict:
    """修理记录：每个补丁的问题/依据/修法（人话），逐条可查。"""
    patches = _load_json(_bank_paths()["patches"])
    return {
        "total": len(patches),
        "note": "原始 CSV 永不修改。每个补丁记录『问题是什么 / 为什么是问题（依据）/ "
                "怎么修的』。新 CSV 上传后补丁自动重放；若某题在源头已修复，"
                "对应补丁会报 patch_failed——属正常信号，复核后删除该补丁即可。",
        "items": [
            {
                "qid": p["qid"],
                "category": p.get("category", ""),
                "problem": p.get("problem", ""),
                "diagnosis": p.get("diagnosis", ""),
                "fix": p.get("fix", ""),
                "ops_count": len(p.get("ops", [])),
            }
            for p in patches
        ],
    }


@router.get("/questions")
def questions_list() -> dict:
    """题目列表（轻量行）。"""
    questions = _load_jsonl(_bank_paths()["jsonl"])
    return {
        "total": len(questions),
        "items": [
            {
                "qid": q["qid"],
                "task_type": q.get("task_type"),
                "scene": q.get("scene"),
                "age_band": q.get("age_band"),
                "difficulty_zh": q.get("difficulty_zh"),
                "judgment_mode": q.get("judgment_mode"),
                "patched": q.get("patched", False),
                "n_turns": len(q.get("turns", [])),
                "n_current": len(q.get("current_session", [])),
                "n_gold": len(q.get("gold_memory_ids", [])),
                "hypotheses_status": q.get("hypotheses_status", ""),
            }
            for q in questions
        ],
    }


@router.get("/questions/{qid}")
def question_detail(qid: str) -> dict:
    """单题详情：切分结果、gold 锚定、判分命题、补丁记录——这道题分数怎么来的全在这。"""
    questions = _load_jsonl(_bank_paths()["jsonl"])
    q = next((x for x in questions if x["qid"] == qid), None)
    if q is None:
        raise HTTPException(404, f"题目 {qid} 不在当前题库 jsonl 中")
    patches = _load_json(_bank_paths()["patches"])
    patch = next((p for p in patches if p["qid"] == qid), None)
    hyps = _load_json(_bank_paths()["hypotheses"])
    hyp = next((h for h in hyps if h["qid"] == qid), None)
    return {
        "question": q,
        "patch": patch,
        "hypotheses_review": {
            "status": hyp.get("status"),
            "review_note": hyp.get("review_note", ""),
            "source_expected_raw": hyp.get("source_expected_raw", ""),
        } if hyp else None,
    }


@router.post("/upload")
async def upload_csv(file: UploadFile) -> dict:
    """上传新版题库 CSV → 跑转换 + 门禁（纯解析，秒级，不调任何 LLM）。

    产物存 data/uploads/{时间戳}/，不覆盖正式题库。正式启用由人工确认后替换。
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "只接受 .csv 文件（从飞书表导出）")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")
    if not content.strip():
        raise HTTPException(400, "文件为空")

    ts = time.strftime("%Y%m%d_%H%M%S")
    upload_dir = UPLOADS_PATH / ts
    upload_dir.mkdir(parents=True, exist_ok=True)
    csv_path = upload_dir / (file.filename or "upload.csv")
    csv_path.write_bytes(content)

    try:
        from kidsbench.questionbank.converter import convert
        paths = _bank_paths()
        result = convert(csv_path, upload_dir, paths["patches"], paths["hypotheses"])
    except Exception as e:  # 上传数据不可信，解析失败要给人话错误
        raise HTTPException(422, f"转换失败：{e}") from e

    from collections import Counter
    issue_kinds = Counter(i.kind for i in result.issues)
    patch_failed = [
        {"qid": i.qid, "detail": i.detail}
        for i in result.issues if i.kind == "patch_failed"
    ]
    report = {
        "uploaded_at": ts,
        "filename": file.filename,
        "stored_dir": str(upload_dir),
        "healthy_questions": len(result.questions),
        "patched": len(result.patched_qids),
        "reclassified": result.reclassified,
        "skipped_non_memory": len(result.skipped),
        "issues_total": len(result.issues),
        "issues_by_kind": dict(issue_kinds),
        "issues": [
            {"qid": i.qid, "kind": i.kind, "detail": i.detail}
            for i in result.issues
        ][:200],
        "patch_failed_note": (
            "以下补丁与新 CSV 失配——很可能这些题在飞书源头已经修复，"
            "请复核后从补丁文件中删除对应条目" if patch_failed else ""),
        "patch_failed": patch_failed,
        "next_step": "本次转换产物已存档，未替换正式题库。确认无误后由维护者"
                     "把 jsonl 替换进 questions/ 并重新部署。",
    }
    (upload_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


@router.get("/export-analysis")
def export_analysis(run_group: str | None = None) -> PlainTextResponse:
    """导出人话分析报告 MD（题库问题/评测诊断/改善建议及论证）。

    ?run_group=xxx 指定分析哪次运行；缺省取最新一次。
    """
    from fastapi.responses import PlainTextResponse

    from .config import RUNS_PATH
    from .qb_report import build_analysis_md

    md = build_analysis_md(QUESTIONS_PATH, RUNS_PATH, run_group)
    ts = time.strftime("%Y%m%d_%H%M")
    return PlainTextResponse(
        md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="kidsbench_analysis_{ts}.md"'})
