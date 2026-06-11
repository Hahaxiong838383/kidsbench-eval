"""飞书题库 CSV → 执行层 jsonl 转换器（协议 v1.1）。

协议（2026-06-11 川哥裁决：小可 runtime 当场对话在 LLM context 里）：
- T-Nd 跨会话历史 → turns[]（逐条 write 进记忆系统）
- T+0 当场对话   → current_session[]（read 时原文进 prompt，不写记忆）
- 记忆题 gold 必须位于 T-Nd 历史（在 T+0 → 当场题，进修题清单）

输入:  questions/raw/v01_memory_20260611.csv（飞书表版本快照）
输出:  questions/v01_memory.jsonl        （健康题，机器可跑）
       questions/v01_memory_issues.csv   （修题清单，给题库侧）

用法:  python scripts/convert_bitable_csv.py [--csv PATH] [--out-dir questions]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- 常量

TZ = timezone(timedelta(hours=8))
# 固定基准日（T+0 所在天），保证 timestamp 可复现
ANCHOR = datetime(2026, 6, 1, 0, 0, tzinfo=TZ)

# turn 行格式变体（实测覆盖 197 题）:
#   [T+0 16:18] / [T-1d 16:30] / [T+0 周六 15:20] / T-0d 08:00（无括号）
_TURN_RE = re.compile(
    r"^\[?T([+-]\d+)d?\s*(?:周[一二三四五六日天]\s*)?(\d{1,2}):(\d{2})\]?\s*"
    r"([^:：]{1,12})[:：]\s*(.*)$"
)
# 系统事件行：[T+0 16:34] [系统已通知监护人]
_SYSTEM_RE = re.compile(
    r"^\[?T([+-]\d+)d?\s*(?:周[一二三四五六日天]\s*)?(\d{1,2}):(\d{2})\]?\s*"
    r"(\[[^\]]+\])\s*$"
)
# 元注释行：(T-5d至T-1d 无使用记录) / (设备重置) / (换新设备)
_META_RE = re.compile(r"^[（(].*[)）]$")

_ROLE_ASSISTANT = ("小可",)

_COG_MAP = {
    "episodic": "episodic", "episodic情景": "episodic",
    "semantic": "semantic", "semantic语义": "semantic",
    "procedural": "procedural", "procedural程序": "procedural",
}
_FACT_MAP = {
    "single": "single", "single单事实": "single",
    "distributed": "distributed", "distributed分布式": "distributed",
}
_TASK_MAP = {
    "T1": "T1_recall", "T2": "T2_consistency", "T3": "T3_update",
    "T4": "T4_interference", "T5": "T5_longterm", "T6": "T6_safety",
    "T7": "T7_noise",
}

MEMORY_DIMS = ("④记忆", "⑤一致")

# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class ParsedTurn:
    day_offset: int
    hh: int
    mm: int
    role: str          # user / assistant
    speaker: str       # 原始说话人名
    text: str

    @property
    def timestamp(self) -> int:
        dt = ANCHOR + timedelta(days=self.day_offset, hours=self.hh, minutes=self.mm)
        return int(dt.timestamp())


@dataclass
class Issue:
    qid: str
    kind: str
    detail: str


@dataclass
class ConvertResult:
    questions: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 解析

def parse_history(raw: str) -> tuple[list[ParsedTurn], list[str], list[str]]:
    """对话历史自由文本 → (turns, meta_notes, unparsed)。

    session 切分按 day_offset 变化推导，不依赖 '---' 分隔符。
    """
    turns: list[ParsedTurn] = []
    meta_notes: list[str] = []
    unparsed: list[str] = []
    for chunk in re.split(r"//|---", raw):
        line = chunk.strip()
        if not line:
            continue
        m = _TURN_RE.match(line)
        if m:
            off, hh, mm, speaker, text = m.groups()
            speaker = speaker.strip()
            role = "assistant" if any(k in speaker for k in _ROLE_ASSISTANT) else "user"
            turns.append(ParsedTurn(
                day_offset=int(off), hh=int(hh), mm=int(mm),
                role=role, speaker=speaker,
                text=text.strip().strip('"“”').strip(),
            ))
            continue
        sm = _SYSTEM_RE.match(line)
        if sm:
            off, hh, mm, event = sm.groups()
            turns.append(ParsedTurn(
                day_offset=int(off), hh=int(hh), mm=int(mm),
                role="system", speaker="system", text=event.strip()))
        elif _META_RE.match(line):
            meta_notes.append(line)
        else:
            unparsed.append(line[:80])
    return turns, meta_notes, unparsed


def parse_scene_context(raw: str) -> dict[str, str]:
    """'用户=朵朵(8岁) ｜ 时间=...' 竖线串 → dict。"""
    ctx: dict[str, str] = {}
    for part in re.split(r"[｜|]", raw):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            ctx[k.strip()] = v.strip()
    return ctx


def _gold_fragment(gold_raw: str) -> str:
    """gold 引用 → 用于匹配的正文片段。"""
    frag = re.sub(r"^\[[^\]]+\]\s*[^:：]{1,12}[:：]\s*", "", gold_raw.strip())
    return frag.strip().strip('"“”').strip()


def locate_gold(gold_raw: str, turns: list[ParsedTurn],
                turn_ids: list[str]) -> str | None:
    """gold 句子模糊匹配回填 turn_id（前缀 12 字符包含匹配）。"""
    frag = _gold_fragment(gold_raw)
    if len(frag) < 4:
        return None
    probe = frag[:12]
    for turn, tid in zip(turns, turn_ids, strict=True):
        if probe in turn.text or turn.text[:12] in frag:
            return tid
    return None


# ---------------------------------------------------------------- 字段清洗

def clean_enum(raw: str, mapping: dict[str, str]) -> str | None:
    key = raw.strip()
    if key in ("", "—", "-"):
        return None
    return mapping.get(key)


# ---------------------------------------------------------------- 单题转换

def convert_row(row: dict[str, str], result: ConvertResult) -> None:
    qid = row.get("题目编号", "").strip()
    dim = row.get("主测维度", "").strip()

    if not qid:
        result.issues.append(Issue("(无编号)", "missing_qid", "题目编号为空"))
        return
    if dim not in MEMORY_DIMS:
        result.skipped.append(qid)
        return

    raw_issues_before = len(result.issues)

    # --- 对话历史
    turns, meta_notes, unparsed = parse_history(row.get("对话历史", ""))
    for u in unparsed:
        result.issues.append(Issue(qid, "unparsed_turn", u))
    if not turns:
        result.issues.append(Issue(qid, "empty_history", "对话历史无可解析 turn"))
        return

    # --- 协议 v1.1 切分：T-Nd → turns（write）；T+0 → current_session（context）
    history = [t for t in turns if t.day_offset < 0]
    current = [t for t in turns if t.day_offset >= 0]

    hist_ids = [f"t_{i+1:03d}" for i in range(len(history))]
    cur_ids = [f"c_{i+1:03d}" for i in range(len(current))]

    # --- gold 回填
    gold_raw = row.get("该想起哪句 gold_memory", "").strip()
    gold_id: str | None = None
    if gold_raw in ("", "—", "-"):
        result.issues.append(Issue(qid, "gold_empty", "gold_memory 为空"))
    else:
        gold_id = locate_gold(gold_raw, history, hist_ids)
        if gold_id is None:
            in_current = locate_gold(gold_raw, current, cur_ids)
            if in_current:
                result.issues.append(Issue(
                    qid, "gold_in_current_session",
                    f"当场题：gold 位于 T+0（{_gold_fragment(gold_raw)[:30]}），"
                    "协议 v1.1 下记忆系统无用武之地。修法=补 T-Nd 历史并把 gold 挪入，"
                    "或重标为行为/一致题移出记忆轨"))
            else:
                result.issues.append(Issue(
                    qid, "gold_not_found",
                    f"gold 无法定位到任何 turn: {_gold_fragment(gold_raw)[:40]}"))

    # --- 枚举清洗
    cog = clean_enum(row.get("记忆类型 cognitive_type", ""), _COG_MAP)
    if cog is None:
        result.issues.append(Issue(qid, "cognitive_type_invalid",
                                   f"值={row.get('记忆类型 cognitive_type', '')!r}"))
    fact = clean_enum(row.get("事实分布 fact_distribution", ""), _FACT_MAP)
    if fact is None:
        result.issues.append(Issue(qid, "fact_distribution_invalid",
                                   f"值={row.get('事实分布 fact_distribution', '')!r}"))

    task_raw = row.get("主考能力 task_type", "").strip()
    task = _TASK_MAP.get(task_raw)
    if task is None:
        result.issues.append(Issue(qid, "task_type_invalid", f"值={task_raw!r}"))

    src = row.get("题目来源 source", "").strip()
    if "synthetic" not in src and "human" not in src and "real" not in src:
        result.issues.append(Issue(qid, "source_invalid", f"值={src[:40]!r}"))

    # --- 触发输入 → query
    query = row.get("触发输入", "").strip()
    if not query:
        result.issues.append(Issue(qid, "query_empty", "触发输入为空"))

    # --- 阻断性问题判断：gold 不健康 / 历史为空 → 不进 jsonl
    blocking = {"gold_empty", "gold_in_current_session", "gold_not_found",
                "empty_history", "query_empty"}
    has_blocking = any(i.kind in blocking for i in result.issues[raw_issues_before:])
    if has_blocking:
        return

    # --- current_timestamp = 最后一个 turn 之后 60s
    last_ts = max(t.timestamp for t in turns)

    question = {
        "qid": qid,
        "task_type": task or "unspecified",
        "scene": row.get("场景", "").strip(),
        "primary_dim": dim,
        "secondary_dim": row.get("副测维度", "").strip(),
        "age_band": row.get("年龄", "").strip(),
        "difficulty_zh": row.get("难度", "").strip(),
        "cognitive_type": cog or "unspecified",
        "fact_distribution": fact or "single",
        "source": "synthetic",
        "turns": [
            {"turn_id": tid, "session_id": f"s_d{abs(t.day_offset)}",
             "role": t.role, "speaker": t.speaker, "text": t.text,
             "timestamp": t.timestamp}
            for t, tid in zip(history, hist_ids, strict=True)
        ],
        "current_session": [
            {"turn_id": tid, "role": t.role, "speaker": t.speaker,
             "text": t.text, "timestamp": t.timestamp}
            for t, tid in zip(current, cur_ids, strict=True)
        ],
        "session_events": meta_notes,
        "query": query,
        "current_timestamp": last_ts + 60,
        "gold_memory_ids": [gold_id] if gold_id else [],
        "gold_turn_rationale": gold_raw,
        # 人话层原文保留（hypothesis 化为 Phase 2，LLM 草稿 + 人审）
        "expected_facts_raw": row.get("答对要点 expected_facts", "").strip(),
        "negative_facts_raw": row.get("答错雷区 negative_facts", "").strip(),
        "expected_facts": [],
        "negative_facts": [],
        "rubric_anchors": {
            "0": row.get("0分锚点", "").strip(),
            "1": row.get("1分锚点", "").strip(),
            "2": row.get("2分锚点", "").strip(),
            "3": row.get("3分锚点", "").strip(),
        },
        "eval_points": row.get("评测要点", "").strip(),
        "root_goal": row.get("根本目标", "").strip(),
        "scene_context": parse_scene_context(row.get("场景上下文", "")),
        "scene_tier": row.get("场景层级", "").strip(),
    }
    result.questions.append(question)


# ---------------------------------------------------------------- 红线扫描

def scan_redlines(result: ConvertResult) -> None:
    """出题铁律门禁：scene_context 泄 gold + gold 必须在跨会话历史。"""
    for q in result.questions:
        gold_ids = set(q["gold_memory_ids"])
        gold_texts = [t["text"] for t in q["turns"] if t["turn_id"] in gold_ids]
        ctx_blob = json.dumps(q["scene_context"], ensure_ascii=False)
        for gt in gold_texts:
            probe = gt[:12]
            if len(probe) >= 6 and probe in ctx_blob:
                result.issues.append(Issue(
                    q["qid"], "redline_gold_in_scene",
                    f"scene_context 包含 gold 片段: {probe}"))


# ---------------------------------------------------------------- 主流程

def convert(csv_path: Path, out_dir: Path) -> ConvertResult:
    result = ConvertResult()
    with csv_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            convert_row(row, result)
    scan_redlines(result)

    redline_qids = {i.qid for i in result.issues if i.kind.startswith("redline")}
    result.questions = [q for q in result.questions if q["qid"] not in redline_qids]

    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "v01_memory.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for q in result.questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    issues_path = out_dir / "v01_memory_issues.csv"
    with issues_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["题目编号", "问题类型", "详情"])
        for i in sorted(result.issues, key=lambda x: (x.kind, x.qid)):
            w.writerow([i.qid, i.kind, i.detail])
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default="questions/raw/v01_memory_20260611.csv")
    ap.add_argument("--out-dir", default="questions")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"❌ CSV 不存在: {csv_path}", file=sys.stderr)
        return 1

    result = convert(csv_path, Path(args.out_dir))

    from collections import Counter
    kinds = Counter(i.kind for i in result.issues)
    print(f"✅ 转换完成: {len(result.questions)} 题进入 jsonl")
    print(f"⏭️  跳过(非④⑤主测): {len(result.skipped)} 题")
    print(f"⚠️  问题: {len(result.issues)} 条 / 涉及 "
          f"{len({i.qid for i in result.issues})} 题")
    for kind, n in kinds.most_common():
        print(f"   - {kind}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
