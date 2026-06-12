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

def _diag_nomemory_leak(rows: list[dict],
                        questions: list[dict] | None = None) -> list[str]:
    """泄露探测：NoMemory（完全没有记忆）答对的题。

    白话依据：题目按「不看记忆猜不出」设计，没记忆还答对 = 答案泄露在
    场景/当场对话里，或题目可被常识猜中 → 该题区分度为零，要打回修题。
    例外：该遗忘型题（negative_only，如 S08 周报「不该提负面旧事」）——
    NoMemory 没记忆天然不会翻旧账、必然得分，不算泄露，排除在外。
    """
    neg_only = {q["qid"] for q in (questions or [])
                if q.get("judgment_mode") == "negative_only"}
    leaks = [r["qid"] for r in rows
             if r["adapter"] == "nomemory" and r.get("judge_verdict") == "correct"
             and r["qid"] not in neg_only]
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

    # ---- 判分三态说明
    from .questionbank import VERDICT_EXPLAINED as _VE
    add("\n## 判分怎么算（答对 / 答错 / 回避）")
    add(f"\n{_VE['intro']}\n")
    for s in _VE["states"]:
        add(f"- **{s['label']}（{s['key']}）**：{s['plain']} 例：{s['example']}")
    add("\n**『回避』的双向含义**：")
    for m in _VE["evasive_meaning"]:
        add(f"- {m}")
    add(f"\n> {_VE['why_so_many']}")

    # ---- 二点五、评测总榜（跨 run 聚合）
    lb = build_leaderboard(runs_path, questions)
    if lb["board"]:
        add("\n## 评测总榜（最近一批全量，按平均分排序）")
        add(f"\n> 聚合自：{', '.join(lb['groups'])}\n")
        add("| 系统 | 平均分 | 全对 | 答错 | 回避 | 召回率 | 内部开销(tokens) | 一句话定位 |")
        add("|---|---|---|---|---|---|---|---|")
        for b in lb["board"]:
            tok = (b["token_note"] or
                   f"写 {b['write_tokens']:,} / 读 {b['read_tokens']:,}")
            add(f"| {b['adapter']} | **{b['avg_score']}** | {b['correct']} | "
                f"{b['wrong']} | {b['evasive']} | {b['avg_recall']} | {tok} | "
                f"{b['plain']} |")
        add("\n### 这轮跑出来的发现（规则自动生成，每条带依据）\n")
        for fi in lb["findings"]:
            add(f"- **{fi['title']}**\n  {fi['why']}")

    # ---- 范式×题型覆盖（出题优化地图）
    from .questionbank import build_paradigm_coverage
    pc = build_paradigm_coverage(questions)
    add("\n## 范式 × 题型覆盖（出题优化地图）")
    add(f"\n{pc['principle']}\n")
    add("| 题型 | 现有 | 建议 | 状态 | 主场受影响的范式 |")
    add("|---|---|---|---|---|")
    for c in pc["coverage"]:
        add(f"| {c['name']} | {c['count']} | ≥{c['minimum']} | {c['status']} | "
            f"{('、'.join(c['starved_paradigms'])) or '—'} |")
    if pc["suggestions"]:
        add("\n**给出题侧的补题优先级**：")
        for i, s in enumerate(pc["suggestions"], 1):
            add(f"{i}. {s}")
    add("\n各范式主场依据：")
    for h in pc["home_ground"]:
        add(f"- **{h['adapter']}**（{h['paradigm']}）主场 {('、'.join(h['home']))}："
            f"{h['why']}")

    # ---- 历史对比
    snaps = load_snapshots(runs_path)
    if snaps:
        mx = history_matrix(snaps)
        add("\n## 评测历史对比（快照矩阵）")
        add("\n> 每跑完一轮评测归档一份快照。**题数不同的快照不可直接比分**"
            "（12 题调试轮 vs 149 题全量是不同口径）。\n")
        header = "| 系统 | " + " | ".join(
            f"{c['created_at'][5:]}（{c['n_questions']}题）" for c in mx["columns"]) + " |"
        add(header)
        add("|" + "---|" * (len(mx["columns"]) + 1))
        for a in mx["adapters"]:
            vals = " | ".join("—" if v is None else f"{v:.3f}"
                              for v in mx["cells"][a])
            add(f"| {a} | {vals} |")
        add("\n各快照环境备注：")
        for s in reversed(snaps):
            add(f"- **{s['created_at']}** · {s['label']} · {s['n_questions']} 题"
                + (f"　｜　{s['notes']}" if s.get("notes") else ""))

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
        for line in _diag_nomemory_leak(rows, questions):
            add(f"- {line}")
        for fn in (_diag_fullhistory_ceiling, _diag_errors, _diag_need_human):
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


# ---------------------------------------------------------------- 评测榜单（跨 run 聚合）

# 每个系统的人话定位（榜单注释，新接入系统时在此登记）
ADAPTER_PLAIN = {
    "nomemory": "地板参照：完全没有记忆。它答对的题=泄露或可猜中，是题目的问题",
    "fullhistory": "全文上限参照：把全部历史原文塞给模型，贵且不现实，但能测出『题目本身可不可答』",
    "oracle": "最小充分信息参照：只喂标准答案那句。真实系统超过它说明『多召回的上下文也有价值』",
    "mem0": "LLM 抽取式：写入时用大模型把对话提炼成事实再存",
    "memoryos": "分层存储式：短期/中期/长期三层记忆架构，按热度晋升",
    "graphiti": "知识图谱式：把对话抽成实体关系图，强项是时序推理（本批 T3 题极少，发挥不出）",
    "hindsight-recall": "四路检索·早绑定：写入时深加工，读取纯检索零大模型调用",
    "hindsight-reflect": "四路检索·晚绑定：读取时再用大模型把记忆合成成答案素材，成本最高",
    "reme": "agentic 多轮检索·晚绑定变体：小 agent 反复查记忆边查边想再综合，中文原生，召回最全",
    "letta": "MemGPT 自管理记忆·archival 直插：agent 像管内存一样管记忆，溯源最干净（tags 精确 1:1）",
    "memobase": "用户画像中心：把对话异步提炼成『属性+事件』画像，读的是画像状态不是历史 turn，中文原生",
    "memmachine": "真值保存式：原文+句级索引不可变账本，写时不抽取不压缩，读取短期∪长期并集，抗幻觉对照组",
    "cognee": "双库多跳式：原文抽实体建知识图谱+向量库，检索做 k-hop 邻域投影，擅长跨话题多跳联想",
}


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _recall_value(r: dict) -> float:
    m = r.get("recall_metric")
    if isinstance(m, dict):
        return float(m.get("recall") or 0)
    return float(m or 0)


def build_leaderboard(runs_path: Path, questions: list[dict],
                      prefix: str = "v01_full") -> dict:
    """跨 run group 聚合总榜 + 自动发现。

    多 venv 分批跑时每家系统各占一个 run 目录（v01_full_mem0 / _graphiti…），
    本函数按目录名前缀扫描合并。发现（findings）由规则自动生成——
    每条规则的依据写成人话直接放进文案，不靠每轮手写。
    """
    rows_by_adapter: dict[str, list[dict]] = {}
    groups: list[str] = []
    if runs_path.exists():
        for d in sorted(runs_path.iterdir()):
            if not d.is_dir() or not d.name.startswith(prefix):
                continue
            f = d / "results.jsonl"
            if not f.exists():
                # 本地两层结构（run_<ts>/results.jsonl）取最新
                subs = sorted(d.glob("run_*/results.jsonl"))
                if not subs:
                    continue
                f = subs[-1]
            groups.append(d.name)
            for line in f.open(encoding="utf-8"):
                if not line.strip():
                    continue
                r = json.loads(line)
                rows_by_adapter.setdefault(r["adapter"], []).append(r)

    board = []
    for a, rows in rows_by_adapter.items():
        n = len(rows)
        verdicts = Counter(r.get("judge_verdict") for r in rows)
        write_tok = sum(r.get("adapter_write_tokens") or 0 for r in rows)
        read_tok = sum(r.get("adapter_read_tokens") or 0 for r in rows)
        board.append({
            "adapter": a,
            "plain": ADAPTER_PLAIN.get(a, ""),
            "n": n,
            "avg_score": round(_avg([r.get("judge_score") or 0 for r in rows]), 3),
            "correct": verdicts.get("correct", 0),
            "wrong": verdicts.get("wrong", 0),
            "evasive": verdicts.get("evasive", 0),
            "error": sum(1 for r in rows if not r.get("success", True)),
            "avg_recall": round(_avg([_recall_value(r) for r in rows]), 2),
            "write_tokens": write_tok,
            "read_tokens": read_tok,
            "token_note": ("未上报（adapter 计量待补）"
                           if (write_tok == 0 and read_tok == 0
                               and a not in ("nomemory", "fullhistory", "oracle"))
                           else ""),
        })
    board.sort(key=lambda x: -x["avg_score"])

    findings = _auto_findings(board, rows_by_adapter, questions)
    return {"groups": groups, "board": board, "findings": findings}


def _auto_findings(board: list[dict], rows_by_adapter: dict,
                   questions: list[dict]) -> list[dict]:
    """从榜单数据自动生成人话发现。每条带 why（依据）。"""
    by = {b["adapter"]: b for b in board}
    findings: list[dict] = []

    oracle = by.get("oracle")
    if oracle:
        beat = [b["adapter"] for b in board
                if b["avg_score"] > oracle["avg_score"]
                and b["adapter"] not in ("oracle",)]
        if beat:
            findings.append({
                "title": f"{'、'.join(beat)} 的平均分超过了 Oracle 参照",
                "why": "Oracle 只喂『标准答案那一句』。真实系统多召回的周边上下文"
                       "反而帮模型把回答组织得更好——Oracle 是『最小充分信息』参照，"
                       "不是绝对上限，对比时要按这个含义解读。",
            })

    hr, hf = by.get("hindsight-recall"), by.get("hindsight-reflect")
    if hr and hf and hf["read_tokens"] > 100_000:
        findings.append({
            "title": "晚绑定（reflect）性价比显著低于早绑定（recall）",
            "why": f"reflect 读取时额外消耗 {hf['read_tokens']:,} tokens 做大模型合成，"
                   f"平均分（{hf['avg_score']}）反而低于零读取成本的 recall"
                   f"（{hr['avg_score']}）。本批题以基础召回/一致性为主，"
                   "读时深加工没有用武之地——该结论是方向性趋势，时序/长程压力题"
                   "（T3/T5）补齐后需复测。",
        })

    task_counts = Counter(q.get("task_type") for q in questions)
    g = by.get("graphiti")
    if g and task_counts.get("T3_update", 0) < 10:
        findings.append({
            "title": "知识图谱（graphiti）排名靠后，但本批题测不出它的强项",
            "why": f"graphiti 的优势是时序推理与矛盾更新，而本批题 T3 矛盾更新仅 "
                   f"{task_counts.get('T3_update', 0)} 题、T5 长程为 0——"
                   "它的主场题型缺席。『这批题上弱』不等于『范式没价值』，"
                   "补齐 T3/T5 题后必须复测再下结论。",
        })

    nm = by.get("nomemory")
    if nm and board:
        top = board[0]
        findings.append({
            "title": f"区分度：最强系统（{top['adapter']} {top['avg_score']}）与"
                     f"无记忆地板（{nm['avg_score']}）拉开 "
                     f"{round(top['avg_score'] - nm['avg_score'], 3)} 分",
            "why": "地板与顶部的差值就是『记忆系统的价值空间』。差值越大，"
                   "题库越能区分系统好坏。同时整体天花板仍偏低（大量回答"
                   "未完全命中多条命题）——回答字数限制与多命题全蕴含口径"
                   "是当前主要压制因素。",
        })

    hr_only = by.get("hindsight-reflect")
    if hr_only and hr_only["avg_recall"] == 0:
        findings.append({
            "title": "reflect 模式召回率显示 0 是统计口径问题，不是真没召回",
            "why": "reflect 返回的是大模型合成后的文本，没有原始记录编号可溯源，"
                   "按编号匹配的召回率算法对它不适用。看它的平均分即可。",
        })

    # 区分度窄 → 范式差距未拉开（数据驱动：补 T3/T5 重跑后区分度拉开此发现会自动变化）
    _BASELINES = {"nomemory", "fullhistory", "oracle"}
    real = [b for b in board if b["adapter"] not in _BASELINES]
    if len(real) >= 4:
        spread = round(real[0]["avg_score"] - real[-1]["avg_score"], 3)
        scarce = [t for t in ("T3_update", "T4_interference", "T5_longterm",
                              "T7_noise")
                  if task_counts.get(t, 0) < 10]
        if spread < 0.15:
            scarce_zh = {"T3_update": "矛盾更新", "T4_interference": "干扰召回",
                         "T5_longterm": "长程抗压", "T7_noise": "脏数据"}
            scarce_names = "、".join(scarce_zh[t] for t in scarce) or "（暂无）"
            findings.append({
                "title": f"⚠️ 范式差距未拉开：{len(real)} 个真实记忆系统挤在 "
                         f"{real[-1]['avg_score']}–{real[0]['avg_score']} 窄区间"
                         f"（极差仅 {spread}）",
                "why": "这不代表『几个系统都一样好』，而是当前题库还没出能区分它们的题。"
                       f"原因：题型集中在 T1 跨会话召回 + T2 一致性，而能拉开范式差距的"
                       f"题型（{scarce_names}）数量不足——graphiti 的时序、各家的抗膨胀、"
                       f"多跳关联等强项都没有用武之地（见范式×题型覆盖地图）。"
                       "下一步：题库侧补这些题型后重跑，区分度会拉开，本提示也会随之消失。"
                       "在那之前，榜单名次差异小，不宜下『某系统更好』的强结论。",
            })
    return findings


# ---------------------------------------------------------------- 历史快照

def snapshot_dir(runs_path: Path) -> Path:
    return runs_path / "history"


def save_snapshot(runs_path: Path, questions: list[dict], prefix: str,
                  label: str, notes: str = "") -> dict:
    """把当前聚合榜单归档为历史快照。

    为什么用显式快照而不是每次现算：①跑批目录会清理/覆盖，快照永久留存；
    ②快照带人话 label 和环境备注（如『prompt 双模式上线后首跑』），
    对比时知道每次的口径差异；③不同批次题数不同（12 题 smoke vs 149 全量），
    快照记录口径，前端对比时标注防误比。
    """
    lb = build_leaderboard(runs_path, questions, prefix)
    if not lb["board"]:
        raise ValueError(f"前缀 {prefix!r} 下没有可聚合的 run 数据")
    n_q = max(b["n"] for b in lb["board"])
    sid = time.strftime("%Y%m%d_%H%M") + "_" + prefix.replace("/", "_")
    snap = {
        "snapshot_id": sid,
        "label": label,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "prefix": prefix,
        "n_questions": n_q,
        "notes": notes,
        "groups": lb["groups"],
        "board": lb["board"],
        "findings": lb["findings"],
    }
    d = snapshot_dir(runs_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return snap


def load_snapshots(runs_path: Path) -> list[dict]:
    """按时间倒序加载全部历史快照。"""
    d = snapshot_dir(runs_path)
    if not d.exists():
        return []
    snaps = [json.loads(f.read_text(encoding="utf-8"))
             for f in sorted(d.glob("*.json"), reverse=True)]
    return snaps


def history_matrix(snaps: list[dict]) -> dict:
    """历史对比矩阵：行=系统、列=快照（时间正序）、值=平均分。

    附带最近两次同口径快照的逐系统变化（delta），便于一眼看升降。
    """
    snaps_asc = list(reversed(snaps))
    adapters: list[str] = []
    for s in snaps_asc:
        for b in s["board"]:
            if b["adapter"] not in adapters:
                adapters.append(b["adapter"])
    columns = [{
        "snapshot_id": s["snapshot_id"],
        "label": s["label"],
        "created_at": s["created_at"],
        "n_questions": s["n_questions"],
    } for s in snaps_asc]
    cells: dict[str, list[float | None]] = {}
    for a in adapters:
        cells[a] = []
        for s in snaps_asc:
            row = next((b for b in s["board"] if b["adapter"] == a), None)
            cells[a].append(row["avg_score"] if row else None)
    # delta：最近两次同题数口径的快照
    delta = {}
    if len(snaps_asc) >= 2:
        last = snaps_asc[-1]
        prev = next((s for s in reversed(snaps_asc[:-1])
                     if s["n_questions"] == last["n_questions"]), None)
        if prev:
            for a in adapters:
                lv = next((b["avg_score"] for b in last["board"]
                           if b["adapter"] == a), None)
                pv = next((b["avg_score"] for b in prev["board"]
                           if b["adapter"] == a), None)
                if lv is not None and pv is not None:
                    delta[a] = round(lv - pv, 3)
            delta["_vs"] = prev["snapshot_id"]
    return {"adapters": adapters, "columns": columns, "cells": cells,
            "delta": delta}
