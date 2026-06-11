"""题库分析报告生成器：题库问题/评测问题/改善建议 → 人话 Markdown。

为什么是 MD 导出（川哥要求，2026-06-11）：benchmark 是持续迭代的系统，
每轮跑完需要一份「人类能看懂」的分析论证文档——题库哪里有问题、跑的
时候踩了什么、下一步怎么改、为什么这样改——给团队（含非工程角色）
传阅、归档、对照迭代。

数据源（全自动汇总，不手写）：
- questions/：jsonl 健康度 + 补丁记录 + 修题清单
- runs/<group>/results.jsonl：每题每系统的判分明细
诊断规则全部显性写在 _RUN_DIAGNOSTICS 各函数 docstring 里。
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 运行诊断规则
# 每条规则：吃 results 行，吐「人话发现」。规则本身就是文档（白话写清依据）。

def _diag_nomemory_leak(rows: list[dict]) -> list[str]:
    """泄露探测：NoMemory（完全没有记忆）答对的题。

    白话依据：题目按「不看记忆猜不出」设计，没记忆还答对 = 答案泄露在
    场景/当场对话里，或题目可被常识猜中 → 该题区分度为零，要打回修题。
    """
    leaks = [r["qid"] for r in rows
             if r["adapter"] == "nomemory" and r.get("judge_verdict") == "correct"]
    if not leaks:
        return ["✅ NoMemory 基线零答对——没有题目泄露答案，区分度健康。"]
    return [f"🔴 **疑似泄露 {len(leaks)} 题**（NoMemory 没有任何记忆却答对了，"
            f"说明答案藏在场景/当场对话里或可被常识猜中）：{', '.join(leaks)}。"
            "建议：逐题检查场景上下文与当场对话，把泄露信息移除或重设 gold。"]


def _diag_fullhistory_ceiling(rows: list[dict]) -> list[str]:
    """上限校准：FullHistory（全部历史原文塞给模型）的得分。

    白话依据：它是理论上限——历史全给都答不对，说明题目本身有问题
    （gold 写错/命题写错/问题问得模型理解不了），而不是记忆系统的问题。
    """
    fh = [r for r in rows if r["adapter"] == "fullhistory"]
    if not fh:
        return []
    wrong = [r["qid"] for r in fh if r.get("judge_verdict") not in ("correct", None)]
    if not wrong:
        return [f"✅ FullHistory 上限 {len(fh)}/{len(fh)} 全对——题目与判分命题自洽。"]
    return [f"🟡 **上限失守 {len(wrong)} 题**（把全部历史原文给模型都没答对，"
            f"大概率是题目或判分命题的问题，不是记忆系统的问题）：{', '.join(wrong)}。"
            "建议：人工看这些题的 answer vs expected_facts，修题或修命题。"]


def _diag_errors(rows: list[dict]) -> list[str]:
    """硬错误：评测过程中抛异常的题（网络/适配器/超时）。"""
    errs = [(r["qid"], r["adapter"], (r.get("error") or "")[:60])
            for r in rows if not r.get("success", True)]
    if not errs:
        return ["✅ 全程无硬错误（无网络/适配器异常）。"]
    return [f"🔴 **硬错误 {len(errs)} 条**（评测环境问题，与题目质量无关，需重跑）：" +
            "；".join(f"{q}@{a}: {e}" for q, a, e in errs[:10])]


def _diag_need_human(rows: list[dict]) -> list[str]:
    """低置信判分：NLI 置信度 <0.7 的判分，需人工抽检确认。"""
    nh = [(r["qid"], r["adapter"]) for r in rows if r.get("nli_need_human")]
    if not nh:
        return ["✅ NLI 判分全部高置信，无需人工抽检。"]
    return [f"🟡 **低置信判分 {len(nh)} 条**（NLI 裁判不确定，结论采纳前需人工"
            f"看一眼答案原文）：{', '.join(f'{q}@{a}' for q, a in nh[:12])}"]


def _diag_evasive(rows: list[dict]) -> list[str]:
    """回避率：模型说「不知道」的占比（按系统分）。

    白话依据：有记忆的系统大量说不知道 = 检索没召回到内容（write 或
    read 链路问题）；NoMemory 说不知道是诚实，是好事。
    """
    by_adapter: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_adapter[r["adapter"]][r.get("judge_verdict") or "?"] += 1
    out = []
    for a, c in sorted(by_adapter.items()):
        total = sum(c.values())
        line = f"- **{a}**: " + " / ".join(f"{v} {k}" for k, v in c.most_common())
        ev = c.get("evasive", 0)
        if a != "nomemory" and total and ev / total > 0.4:
            line += "　⚠️ 回避率过高——检索大概率没召回到内容，查 write/read 链路"
        out.append(line + f"（共 {total}）")
    return out


def _diag_query_gap(rows: list[dict], questions: list[dict]) -> list[str]:
    """事件型触发的检索难度：触发输入是[系统事件]的题 recall 表现。

    白话依据（A 方案的核心观察点）：旧题的提问是自然问句，检索友好；
    新题大量用「[系统事件] 坐姿=歪头」做检索词，与该想起的内容语义距离
    远，召回可能系统性偏低——这反映产品『事件驱动调记忆』的真实难度，
    不是记忆系统的锅。此数据用于决策要不要加『查询改写层』。
    """
    sys_qids = {q["qid"] for q in questions if q.get("query", "").startswith("[系统事件]")}
    if not sys_qids:
        return []
    mem_rows = [r for r in rows if r["adapter"] not in ("nomemory", "fullhistory", "oracle")]
    if not mem_rows:
        mem_rows = [r for r in rows if r["adapter"] == "fullhistory"]
    def _recall(r: dict) -> float:
        # recall_metric 两种历史形态：float（旧 run）或 dict{recall,...}（新 run）
        m = r.get("recall_metric")
        if isinstance(m, dict):
            return float(m.get("recall") or 0)
        return float(m or 0)

    sys_recall = [_recall(r) for r in mem_rows if r["qid"] in sys_qids]
    qa_recall = [_recall(r) for r in mem_rows if r["qid"] not in sys_qids]
    if not sys_recall or not qa_recall:
        return []
    avg = lambda x: sum(x) / len(x)  # noqa: E731
    return [f"📊 事件型触发 vs 自然提问的召回对比：[系统事件] 题平均 recall "
            f"{avg(sys_recall):.2f}（{len(sys_recall)} 行）vs 自然提问题 "
            f"{avg(qa_recall):.2f}（{len(qa_recall)} 行）。"
            "若事件型显著更低，说明『拿事件描述去查记忆』先天吃亏——"
            "下一步该讨论加『查询改写层』（产品 runtime 同样要面对这个问题）。"]


# ---------------------------------------------------------------- 报告主体

def build_analysis_md(questions_path: Path, runs_path: Path,
                      run_group: str | None = None) -> str:
    """汇总题库 + 最新（或指定）run → 人话分析 MD。"""
    questions = _load_jsonl(questions_path / "v01_memory.jsonl")
    patches = _load_json(questions_path / "patches" / "v01_memory_patches.json")
    hyps = _load_json(questions_path / "patches" / "v01_memory_hypotheses.json")

    # 选 run：指定 > 最新含 results.jsonl 的目录
    run_dir = None
    if run_group:
        cand = runs_path / run_group
        if (cand / "results.jsonl").exists():
            run_dir = cand
    else:
        cands = [d for d in runs_path.iterdir()
                 if d.is_dir() and (d / "results.jsonl").exists()] if runs_path.exists() else []
        if cands:
            run_dir = max(cands, key=lambda d: (d / "results.jsonl").stat().st_mtime)
    rows = _load_jsonl(run_dir / "results.jsonl") if run_dir else []

    L: list[str] = []
    add = L.append
    add("# KidsBench 题库与评测分析报告")
    add(f"\n> 生成时间：{time.strftime('%Y-%m-%d %H:%M')} ｜ 题库版本：v0.1_记忆"
        f" ｜ 分析的运行：{run_dir.name if run_dir else '（暂无运行数据）'}")
    add("\n> 本报告由系统自动汇总生成，目的：把『题库哪里有问题、评测跑出了什么问题、"
        "下一步怎么改、为什么』讲成人话，供团队传阅与迭代对照。")

    # ---- 一、题库现状
    add("\n## 一、题库现状")
    n_draft = sum(1 for h in hyps if h.get("status") == "draft")
    add(f"\n- 机器可跑：**{len(questions)} 题**（经 {len(patches)} 个补丁修复其中 "
        f"{sum(1 for q in questions if q.get('patched'))} 题）")
    add(f"- 题型分布：{dict(Counter(q.get('task_type') for q in questions))}")
    add(f"- 带当场对话的题：{sum(1 for q in questions if q.get('current_session'))} "
        "（评测时这部分直接进模型上下文，模拟产品真实运行）")
    add(f"- 判分命题：{sum(len(q.get('expected_facts', [])) for q in questions)} 条事实命题"
        f" + {sum(len(q.get('negative_facts', [])) for q in questions)} 条雷区命题；"
        f"其中 **{n_draft} 题的命题仍是机器草稿（draft）**——正式结论发布前需题库侧终审")

    # ---- 二、题库已知问题与改进
    add("\n## 二、题库的问题与改进记录")
    add("\n原始 CSV 永不修改，所有修复以补丁记录在案（问题/依据/修法三段），按类汇总：\n")
    for cat, n in Counter(p.get("category") for p in patches).most_common():
        example = next(p for p in patches if p.get("category") == cat)
        add(f"- **{cat}**（{n} 题）：{example['problem'][:80]}…")
    add("\n**给出题侧的三条铁律**（从这批修复反推，写进出题规范防再犯）：")
    add("1. 记忆题的『该想起哪句』必须位于跨会话历史（T-Nd）——当场对话里的内容"
        "产品本来就看得见，不构成记忆考点；")
    add("2. gold 必须一字不差引用对话原句，单字确认句（好/嗯）不能当 gold——"
        "机器按句子定位做归因，对不上就没法判分；")
    add("3. 分布式事实的 gold 逐句列出（// 分隔），不写区间引用。")

    # ---- 三、评测运行诊断
    add("\n## 三、本次评测运行诊断")
    if not rows:
        add("\n（暂无运行数据——跑一次评测后重新导出，此节会自动填充）")
    else:
        adapters = sorted({r['adapter'] for r in rows})
        add(f"\n运行 `{run_dir.name}`：{len(rows)} 行结果，"
            f"覆盖系统：{', '.join(adapters)}\n")
        add("### 判分分布（每系统）")
        L.extend(_diag_evasive(rows))
        add("\n### 自动诊断（规则与依据见行内说明）")
        for fn in (_diag_nomemory_leak, _diag_fullhistory_ceiling,
                   _diag_errors, _diag_need_human):
            for line in fn(rows):
                add(f"- {line}")
        for line in _diag_query_gap(rows, questions):
            add(f"- {line}")

    # ---- 四、改善建议与论证
    add("\n## 四、改善建议与论证")
    add("""
1. **当场题不考记忆的逻辑**：产品 runtime 当场对话在模型上下文里（产品事实，
   2026-06-11 裁决）。如果让记忆系统去『召回 30 秒前的话』，测的是一个产品中
   不存在的链路，结论无法外推——所以当场内容直接进上下文，记忆只考跨会话。
2. **判分为什么分两条线**：『提没提到语文听写』是客观事实，NLI 自动判，可复现；
   『语气温不温和』是主观风格，NLI 判不了，硬判会产生伪客观分数——所以风格点
   走 0-3 锚点（人工/裁判模型），两条线分开，各自诚实。
3. **回答模型为什么用国产弱档**（deepseek-v4-flash）：模型太强会靠常识硬猜把
   没记忆的题也答对，掩盖记忆系统差异；国产模型也与产品真实生态一致。
   NLI 裁判独立用 Qwen——被测和裁判不能是同一个模型，避免自己判自己。
4. **已知偏差声明**：本批新题的写入侧对话比真实场景干净（缺闲聊噪声），
   LLM 抽取式记忆系统的写入质量会被略微高估；事件型触发的检索难度数据
   （见第三节）将决定是否引入查询改写层。
5. **结论发布纪律**：探索期只报『方向性趋势』；真实用户种子数据占比达标前，
   不使用『统计显著』表述。

### 评测环境演进记录（按时间倒序，每条带依据）

- **2026-06-11 · 回答 prompt 双模式**：首跑 smoke 发现问答式 prompt 下连
  Oracle（直接喂正确记忆）都 11/12 不使用记忆——危机记忆在场，模型却只回
  『新书包真漂亮』。根因：旧 prompt 让模型『回答用户的问题』，而新题没有
  问题、只有系统事件，模型不知道要主动把记忆融进关怀。修复：事件触发题
  换『小可主动陪伴』prompt（必须自然融入相关记忆+危机警觉），问答式旧题
  prompt 逐字不动。该修复对所有被测系统一致，不影响横评公平。
- **2026-06-11 · 判分部分得分（partial credit）**：新题常带 2-3 条事实命题，
  原口径『全蕴含才得分』导致 3 中 2 也算 0 分，区分度被压扁。升级为
  score=命中比例（单命题题行为不变，verdict/acc 口径不变，零回归）。
""")
    return "\n".join(L) + "\n"
