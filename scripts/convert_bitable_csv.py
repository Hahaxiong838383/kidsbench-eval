"""飞书题库 CSV → 执行层 jsonl 转换器（协议 v1.1）。

协议（2026-06-11 川哥裁决：小可 runtime 当场对话在 LLM context 里）：
- T-Nd 跨会话历史 → turns[]（逐条 write 进记忆系统）
- T+0 当场对话   → current_session[]（read 时原文进 prompt，不写记忆）
- 记忆题 gold 必须位于 T-Nd 历史（在 T+0 → 当场题，进修题清单）

输入:  questions/raw/v01_memory_20260611.csv（飞书表版本快照，只读不改）
       questions/patches/v01_memory_patches.json（修复补丁层，人类可读）
输出:  questions/v01_memory.jsonl        （健康题，机器可跑）
       questions/v01_memory_issues.csv   （修题清单，给题库侧）

修复机制（显性可审计）：原始 CSV 永不修改，全部修复以补丁形式落在
patches 文件里，每个补丁带 problem（问题）/diagnosis（原因）/fix（修法）
三段人话说明。飞书表更新后重新下载 CSV，补丁可重放；补丁与新数据失配
时显式报 patch_failed，不会静默漂移。人类阅读版修理说明见
docs/QUESTIONBANK_V01_FIX_NOTES.md。

会话切分逻辑（协议 v1.1，2026-06-11 川哥裁决）：
- 会话边界 = '---' 分隔符 或 day_offset 变化
- 最后一个会话若发生在 T+0 → current_session（LLM 上下文，不写记忆）
- 其余会话（含 T-Nd 全部）→ turns（逐条 write 进记忆系统）
- 补丁标记 history_all_write 的题：全部历史按已结束会话写入记忆

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
    role: str          # user / assistant / system
    speaker: str       # 原始说话人名
    text: str
    session_idx: int = 0   # 会话序号（'---' 或 day_offset 变化时递增）

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
    reclassified: list[str] = field(default_factory=list)
    patched_qids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 解析

def parse_history(raw: str) -> tuple[list[ParsedTurn], list[str], list[str]]:
    """对话历史自由文本 → (turns, meta_notes, unparsed)。

    会话边界（session_idx 递增）= '---' 分隔符 或 day_offset 变化。
    两个信号都认，因为出题格式不统一：108 题跨会话靠 T-Nd 标记表达，
    只有 34 题用了 '---'。
    """
    turns: list[ParsedTurn] = []
    meta_notes: list[str] = []
    unparsed: list[str] = []
    session_idx = -1
    last_day: int | None = None
    for block in raw.split("---"):
        new_block = True
        for chunk in block.split("//"):
            line = chunk.strip()
            if not line:
                continue

            def _push(off: int, hh: str, mm: str, role: str,
                      speaker: str, text: str) -> None:
                nonlocal session_idx, last_day, new_block
                if new_block or last_day is None or off != last_day:
                    session_idx += 1
                new_block = False
                last_day = off
                turns.append(ParsedTurn(
                    day_offset=off, hh=int(hh), mm=int(mm), role=role,
                    speaker=speaker, text=text, session_idx=session_idx))

            m = _TURN_RE.match(line)
            if m:
                off, hh, mm, speaker, text = m.groups()
                speaker = speaker.strip()
                role = ("assistant" if any(k in speaker for k in _ROLE_ASSISTANT)
                        else "user")
                _push(int(off), hh, mm, role, speaker,
                      text.strip().strip('"“”').strip())
                continue
            sm = _SYSTEM_RE.match(line)
            if sm:
                off, hh, mm, event = sm.groups()
                _push(int(off), hh, mm, "system", "system", event.strip())
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
    """gold 句子模糊匹配回填 turn_id。

    匹配策略（按 gold 正文长度分级，防误匹配）：
    - ≥4 字：前缀 12 字包含匹配（容忍 gold 引用略有截短）
    - 恰 3 字（如「习惯了」）：必须与某 turn 全文精确相等才算命中
      （包含匹配会误命中长句，整句相等无歧义）
    - <3 字（如「好」「嗯」）：拒绝定位 —— 单字确认句作 gold 归因力为零，
      此类 gold 应在补丁/修题层改为信息承载句
    """
    frag = _gold_fragment(gold_raw)
    if len(frag) < 3:
        return None
    if len(frag) == 3:
        for turn, tid in zip(turns, turn_ids, strict=True):
            if turn.text == frag:
                return tid
        return None
    probe = frag[:12]
    for turn, tid in zip(turns, turn_ids, strict=True):
        if probe in turn.text or turn.text[:12] in frag:
            return tid
    return None


def locate_gold_multi(gold_raw: str, turns: list[ParsedTurn],
                      turn_ids: list[str]) -> list[str] | None:
    """多句 gold（'//' 分隔的多条引用，distributed 题）→ 多个 turn_id。

    任何一条引用定位失败即整体失败（宁缺勿错，失败进修题清单）。
    """
    gold_ids: list[str] = []
    for ref in gold_raw.split("//"):
        ref = ref.strip()
        if not ref:
            continue
        tid = locate_gold(ref, turns, turn_ids)
        if tid is None:
            return None
        if tid not in gold_ids:
            gold_ids.append(tid)
    return gold_ids or None


# ---------------------------------------------------------------- 补丁层

def load_patches(path: Path) -> dict[str, dict]:
    """加载修复补丁（qid → patch）。文件不存在时返回空（转换器可独立运行）。"""
    if not path.exists():
        return {}
    patches = json.loads(path.read_text(encoding="utf-8"))
    return {p["qid"]: p for p in patches}


def apply_patch(row: dict[str, str], patch: dict,
                result: ConvertResult) -> tuple[dict[str, str], dict]:
    """对单题应用补丁 ops，返回 (新 row, flags)。

    原 row 不修改（immutable）。op 失配（replace 的 old 不在字段里）
    显式报 patch_failed —— 飞书表更新后补丁漂移会被立刻发现，不会静默。
    """
    qid = patch["qid"]
    new_row = dict(row)
    flags: dict = {}
    for op in patch.get("ops", []):
        kind = op["op"]
        if kind == "reclassify":
            flags["reclassify"] = op["to"]
        elif kind == "mark":
            flags[op["flag"]] = True
        elif kind in ("replace", "replace_all"):
            field_name = op["field"]
            if op["old"] not in new_row.get(field_name, ""):
                result.issues.append(Issue(
                    qid, "patch_failed",
                    f"补丁失配：字段「{field_name}」中找不到 {op['old']!r}"
                    "（飞书表内容可能已更新，需重核补丁）"))
                continue
            new_row[field_name] = new_row[field_name].replace(op["old"], op["new"])
        elif kind == "set":
            new_row[op["field"]] = op["value"]
        else:
            result.issues.append(Issue(qid, "patch_failed", f"未知 op: {kind}"))
    return new_row, flags


# ---------------------------------------------------------------- 字段清洗

def clean_enum(raw: str, mapping: dict[str, str]) -> str | None:
    key = raw.strip()
    if key in ("", "—", "-"):
        return None
    return mapping.get(key)


# ---------------------------------------------------------------- 单题转换

def convert_row(row: dict[str, str], result: ConvertResult,
                flags: dict | None = None) -> None:
    flags = flags or {}
    qid = row.get("题目编号", "").strip()
    dim = row.get("主测维度", "").strip()

    if not qid:
        result.issues.append(Issue("(无编号)", "missing_qid", "题目编号为空"))
        return
    if dim not in MEMORY_DIMS:
        result.skipped.append(qid)
        return
    if flags.get("reclassify"):
        result.reclassified.append(qid)
        return

    raw_issues_before = len(result.issues)

    # --- 对话历史
    turns, meta_notes, unparsed = parse_history(row.get("对话历史", ""))
    for u in unparsed:
        result.issues.append(Issue(qid, "unparsed_turn", u))
    if not turns:
        result.issues.append(Issue(qid, "empty_history", "对话历史无可解析 turn"))
        return

    # --- 协议 v1.1 切分（按会话边界，不按天）：
    #     最后一个会话若在 T+0 → current_session（LLM 上下文）；
    #     其余会话 → turns（write 进记忆）。
    #     history_all_write 标记：触发输入本身是新会话（如重新入座），
    #     全部历史按已结束会话写入。
    last_sess = max(t.session_idx for t in turns)
    last_sess_turns = [t for t in turns if t.session_idx == last_sess]
    last_sess_is_today = all(t.day_offset >= 0 for t in last_sess_turns)
    if flags.get("history_all_write") or not last_sess_is_today:
        history, current = turns, []
    else:
        history = [t for t in turns if t.session_idx < last_sess]
        current = last_sess_turns

    hist_ids = [f"t_{i+1:03d}" for i in range(len(history))]
    cur_ids = [f"c_{i+1:03d}" for i in range(len(current))]

    # --- gold 回填（支持多句 '//' 引用，distributed 题）
    gold_raw = row.get("该想起哪句 gold_memory", "").strip()
    gold_ids: list[str] = []
    if gold_raw in ("", "—", "-"):
        if flags.get("negative_only"):
            # 该遗忘型题：gold 空是设计意图，判分走 negative_facts
            pass
        else:
            result.issues.append(Issue(qid, "gold_empty", "gold_memory 为空"))
    else:
        found = locate_gold_multi(gold_raw, history, hist_ids)
        if found:
            gold_ids = found
        else:
            in_current = locate_gold_multi(gold_raw, current, cur_ids)
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
        "judgment_mode": "negative_only" if flags.get("negative_only") else "standard",
        "patched": bool(flags) or qid in result.patched_qids,
        "turns": [
            {"turn_id": tid, "session_id": f"s{t.session_idx}",
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
        "gold_memory_ids": gold_ids,
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

def merge_hypotheses(result: ConvertResult, hyp_path: Path) -> int:
    """合并 NLI 命题草稿（gen_hypotheses.py 产出）进题目。

    只合并 draft/approved 状态；failed/needs_review 的题保持 expected_facts
    为空（harness 跑到会显式跳过判分，不会静默错判）。
    jsonl 里的 hypotheses_status 字段记录每题命题来源状态，可审计。
    """
    if not hyp_path.exists():
        return 0
    hyps = {h["qid"]: h for h in json.loads(hyp_path.read_text(encoding="utf-8"))}
    merged = 0
    for q in result.questions:
        h = hyps.get(q["qid"])
        if h is None:
            q["hypotheses_status"] = "missing"
            continue
        if h["status"] not in ("draft", "approved"):
            q["hypotheses_status"] = h["status"]
            continue
        q["expected_facts"] = h["expected_facts"]
        q["negative_facts"] = h["negative_facts"]
        q["style_points"] = h.get("style_points", [])
        q["hypotheses_status"] = h["status"]
        merged += 1
    return merged


def convert(csv_path: Path, out_dir: Path, patches_path: Path,
            hypotheses_path: Path) -> ConvertResult:
    result = ConvertResult()
    patches = load_patches(patches_path)
    with csv_path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            qid = row.get("题目编号", "").strip()
            flags: dict = {}
            if qid in patches:
                row, flags = apply_patch(row, patches[qid], result)
                result.patched_qids.append(qid)
            convert_row(row, result, flags)
    scan_redlines(result)
    merge_hypotheses(result, hypotheses_path)

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
    ap.add_argument("--patches", default="questions/patches/v01_memory_patches.json")
    ap.add_argument("--hypotheses",
                    default="questions/patches/v01_memory_hypotheses.json")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"❌ CSV 不存在: {csv_path}", file=sys.stderr)
        return 1

    result = convert(csv_path, Path(args.out_dir), Path(args.patches),
                     Path(args.hypotheses))

    from collections import Counter
    kinds = Counter(i.kind for i in result.issues)
    print(f"✅ 转换完成: {len(result.questions)} 题进入 jsonl"
          f"（其中打补丁修复 {len([q for q in result.questions if q['patched']])} 题）")
    print(f"🩹 应用补丁: {len(result.patched_qids)} 题")
    print(f"➡️  重标移出记忆轨: {len(result.reclassified)} 题 {result.reclassified}")
    print(f"⏭️  跳过(非④⑤主测): {len(result.skipped)} 题")
    print(f"⚠️  剩余问题: {len(result.issues)} 条 / 涉及 "
          f"{len({i.qid for i in result.issues})} 题")
    for kind, n in kinds.most_common():
        print(f"   - {kind}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
