"""expected_facts hypothesis 化（Phase 2）：人话要点 → NLI 可判命题句。

为什么需要这一步：新题库的「答对要点」是人话短语（如「提及语文听写，给予鼓励」），
而 NLI 判分（judge_facts_nli）需要完整的陈述句命题。本脚本用 LLM 批量产草稿，
再经 cc 全量校验 + 抽查（终审权在题库侧）。

事实型 vs 风格型分治（核心策略，显性声明）：
- 事实型要点（提及了什么具体内容）→ expected_facts/negative_facts，走 NLI 蕴含判分
- 风格型要点（语气温和/伙伴感/不说教）→ style_points，NLI 判不了风格，
  走 0-3 锚点 judge 路径（rubric_anchors 已在 jsonl 保留）

输出: questions/patches/v01_memory_hypotheses.json（draft，人类可读，含原始要点对照）
断点续跑: 已有结果的题自动跳过，中断后重跑即续。

用法: python scripts/gen_hypotheses.py [--limit N] [--concurrency 6]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

PROXY_URL = "http://23.226.135.149:4000/v1/chat/completions"
MODEL = "gemini-3.5-flash"

JSONL_PATH = Path("questions/v01_memory.jsonl")
OUT_PATH = Path("questions/patches/v01_memory_hypotheses.json")

SYSTEM_PROMPT = """你是评测题库的判分命题工程师。把一道「儿童学习陪伴 AI」记忆评测题的人话要点，改写为 NLI（自然语言推理）可判定的命题句。判分方式：NLI 模型判「被测 AI 的回答是否蕴含该命题」。

规则（必须全部遵守）：
1. expected_facts：只写**事实型**命题——AI 回答中应包含的具体内容，写成完整陈述句。例：「AI 在回应中提到用户有语文听写」。
2. negative_facts：互斥错误命题（答错雷区中的事实型错误）。涉及数字/名字/科目/近义实体时必须写失真版（如把「语文听写」错说成「数学考试」）。
3. 风格型要点（语气温和/不说教/伙伴感/年龄适配/像老朋友）**绝不**写成 NLI 命题——NLI 判不了风格。列入 style_points 原样保留。
4. 每条命题自包含：写明具体内容（名字/事件原文），不用「上次说的」「该用户」等悬空指代。
5. 严禁引入题目数据之外的新事实（人名/数字/事件必须来自题目给的材料）。
6. expected_facts 1-3 条、negative_facts 0-3 条，宁少勿滥——每条都必须是判分必要的。

只输出 JSON（无 markdown 代码块）：
{"expected_facts":[{"hypothesis":"..."}],"negative_facts":[{"hypothesis":"...","polarity":"mutually_exclusive"}],"style_points":["..."]}"""

FEW_SHOT_USER = """题目数据：
- 该想起的记忆（gold）：孩子说过「明天有语文听写，我好紧张」
- 触发输入：[系统事件] 传感器检测到入座
- 答对要点：提及今天有语文听写，给予鼓励
- 答错雷区：混淆"被表扬"和"听写"信息；编造不存在的考试科目
- 3分锚点：答{正确}+细节（如"昨天练过了，今天听写肯定没问题～"）"""

FEW_SHOT_ASSISTANT = """{"expected_facts":[{"hypothesis":"AI 在回应中提到用户今天有语文听写"},{"hypothesis":"AI 对用户进行了鼓励"}],"negative_facts":[{"hypothesis":"AI 称用户今天有数学考试","polarity":"mutually_exclusive"},{"hypothesis":"AI 把语文听写说成是用户被表扬的事情","polarity":"mutually_exclusive"}],"style_points":[]}"""


def build_user_prompt(q: dict) -> str:
    gold_texts = [t["text"] for t in q["turns"] if t["turn_id"] in q["gold_memory_ids"]]
    gold_line = " / ".join(f"「{t}」" for t in gold_texts) if gold_texts else "（该遗忘型题：没有该想起的，只有不该提的）"
    parts = [
        "题目数据：",
        f"- 该想起的记忆（gold）：{gold_line}",
        f"- 触发输入：{q['query']}",
        f"- 答对要点：{q['expected_facts_raw'] or '（无）'}",
        f"- 答错雷区：{q['negative_facts_raw'] or '（无）'}",
        f"- 3分锚点：{q['rubric_anchors'].get('3','')[:150]}",
        f"- 0分锚点：{q['rubric_anchors'].get('0','')[:150]}",
    ]
    if q["judgment_mode"] == "negative_only":
        parts.append("- 特别说明：本题为该遗忘型，expected_facts 留空数组，重点写 negative_facts（提及该负面事件即错）")
    return "\n".join(parts)


def validate(qid: str, data: dict, q: dict) -> list[str]:
    """草稿校验：schema + 自包含 + 不引入新事实（宽口径警告）。"""
    errors = []
    if not isinstance(data.get("expected_facts"), list):
        errors.append("expected_facts 非 list")
        return errors
    for ef in data["expected_facts"]:
        h = ef.get("hypothesis", "")
        if not h or len(h) < 6:
            errors.append(f"hypothesis 过短: {h!r}")
        for pron in ("上次", "该用户", "之前提到的"):
            if pron in h:
                errors.append(f"悬空指代「{pron}」: {h[:30]}")
    if q["judgment_mode"] == "negative_only" and data["expected_facts"]:
        errors.append("negative_only 题不应有 expected_facts")
    if q["judgment_mode"] == "standard" and not data["expected_facts"]:
        errors.append("standard 题 expected_facts 为空")
    for nf in data.get("negative_facts", []):
        if nf.get("polarity") != "mutually_exclusive":
            errors.append(f"negative polarity 缺失: {nf.get('hypothesis','')[:30]}")
    return errors


async def gen_one(client: httpx.AsyncClient, q: dict, api_key: str,
                  sem: asyncio.Semaphore) -> dict:
    async with sem:
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": FEW_SHOT_USER},
                {"role": "assistant", "content": FEW_SHOT_ASSISTANT},
                {"role": "user", "content": build_user_prompt(q)},
            ],
            "max_tokens": 16384,
            "reasoning_effort": "medium",
            "response_format": {"type": "json_object"},
        }
        for attempt in range(3):
            try:
                resp = await client.post(
                    PROXY_URL, json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=60.0)
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                if not content or len(content) < 10:
                    raise ValueError("空响应（proxy 静默失败模式）")
                # raw_decode 容忍尾随数据（模型偶发输出多余文本）
                data, _ = json.JSONDecoder().raw_decode(content.strip())
                errors = validate(q["qid"], data, q)
                return {
                    "qid": q["qid"],
                    "status": "draft" if not errors else "needs_review",
                    "validation_errors": errors,
                    "source_expected_raw": q["expected_facts_raw"],
                    "source_negative_raw": q["negative_facts_raw"],
                    "expected_facts": data.get("expected_facts", []),
                    "negative_facts": data.get("negative_facts", []),
                    "style_points": data.get("style_points", []),
                }
            except Exception as e:
                if attempt == 2:
                    return {"qid": q["qid"], "status": "failed", "error": str(e)[:120]}
                await asyncio.sleep(2 * (attempt + 1))
        return {"qid": q["qid"], "status": "failed", "error": "unreachable"}


async def main_async(limit: int | None, concurrency: int) -> int:
    api_key = os.environ.get("KIDSBENCH_GEMINI_API_KEY", "")
    if not api_key:
        # .env.local 兜底加载
        env_file = Path(".env.local")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("KIDSBENCH_GEMINI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
    if not api_key:
        print("❌ 缺 KIDSBENCH_GEMINI_API_KEY（env 或 .env.local）", file=sys.stderr)
        return 1

    questions = [json.loads(line) for line in JSONL_PATH.open()]
    done: dict[str, dict] = {}
    if OUT_PATH.exists():
        for item in json.loads(OUT_PATH.read_text()):
            if item.get("status") in ("draft", "needs_review", "approved"):
                done[item["qid"]] = item

    todo = [q for q in questions if q["qid"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"总题 {len(questions)} / 已完成 {len(done)} / 本次生成 {len(todo)}")

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(gen_one(client, q, api_key, sem) for q in todo))

    merged = {**done, **{r["qid"]: r for r in results}}
    ordered = [merged[q["qid"]] for q in questions if q["qid"] in merged]
    OUT_PATH.write_text(json.dumps(ordered, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    from collections import Counter
    stat = Counter(r["status"] for r in ordered)
    print(f"✅ 写入 {OUT_PATH}: {dict(stat)}")
    for r in ordered:
        if r["status"] == "needs_review":
            print(f"  ⚠️ {r['qid']}: {r['validation_errors']}")
        elif r["status"] == "failed":
            print(f"  ❌ {r['qid']}: {r.get('error')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()
    return asyncio.run(main_async(args.limit, args.concurrency))


if __name__ == "__main__":
    sys.exit(main())
