# 题库转换管线机制说明（显性化文档）

> 日期：2026-06-11 ｜ 状态：v1 上线（149 题转换成功）
> 目的：把转换管线的**每一步机制、判定逻辑、失败模式**写成人话，
> 出问题时按本文档排查，不用读代码猜。
> 代码：`scripts/convert_bitable_csv.py` ｜ 修理说明：`QUESTIONBANK_V01_FIX_NOTES.md`

## 全链路图

```
飞书 bitable「测评题库v0.1_记忆」(197 题，活文档)
  │  人工下载 CSV
  ▼
questions/raw/v01_memory_20260611.csv   ← 版本快照，只读永不修改
  │
  ▼
补丁层 questions/patches/v01_memory_patches.json  ← 全部修复在这里，人类可读
  │  逐题应用 ops（replace/set/mark/reclassify）；失配显式报 patch_failed
  ▼
解析层 parse_history()                  ← 自由文本 → 结构化 turn
  │  4 种 turn 格式变体 + 系统事件行 + 元注释
  ▼
协议切分（v1.1）                         ← 决定哪些内容进记忆、哪些进上下文
  │  写入侧 turns[] ｜ 上下文侧 current_session[]
  ▼
gold 回填 locate_gold_multi()           ← gold 句子 → turn_id（归因的基石）
  │
  ▼
红线门禁 scan_redlines()                ← 泄 gold 扫描，命中即拦截
  │
  ▼
questions/v01_memory.jsonl (149 题)  +  questions/v01_memory_issues.csv (修题清单)
  │
  ▼
harness/run_eval.py → 记忆后端横评（mem0 / memoryos / graphiti / hindsight）
```

## 第一性原理：协议 v1.1

**事实裁决（2026-06-11 川哥确认）**：最终产品的小可 runtime，当场对话在 LLM 上下文里。

由此推出三条切分规则：

| 题目数据 | 评测时去哪 | 仿真的产品现实 |
|---|---|---|
| T-Nd 跨会话历史 | `turns[]` → 逐条 write 进记忆系统 | 会话结束后记忆固化 |
| T+0 当场对话 | `current_session[]` → 原文进 prompt | runtime 的 LLM context |
| 触发输入 | `query` → prompt 末尾 | 当下事件 |

**为什么 T+0 不写入记忆**：写不写对本题判分无影响（原文已在 context），但写入会让
刚产生的内容以高 recency 分挤占检索 top-k、污染长程判分——不写，归因最干净。

**为什么 gold 必须在 T-Nd**：gold 在 T+0 意味着任何系统（包括无记忆的裸 LLM）都能
从 context 答对——记忆后端区分度为零，这种题测不出任何东西。

## 会话切分逻辑（最容易出错的地方）

会话边界 = `---` 分隔符 **或** day_offset 变化（出题格式不统一：108 题只用 T-Nd
时间标记表达跨会话、34 题用 `---`，所以两个信号都认）。

切分规则：
- **最后一个会话发生在 T+0** → 它是 `current_session`（当前正在进行的对话）
- **其余全部会话** → `turns[]`（已结束，写入记忆）
- **最后一个会话不在 T+0**（历史全是过去的）→ 全部写入，`current_session` 为空
- **补丁标记 `history_all_write`** → 强制全部写入（用于「触发=重新入座」这种
  触发本身就是新会话开始的题，如 S04-④-010）

排查口诀：**「这段对话在触发发生时还在屏幕上吗？」** 在 → current；不在 → 写记忆。

## turn 解析（4 种格式变体）

| 格式 | 例子 | 处理 |
|---|---|---|
| 标准 | `[T-1d 16:30] 小可: "…"` | 正常解析 |
| 带星期 | `[T+0 周六 15:20] 小可: "…"` | 星期忽略，以 T±Nd 为准 |
| 无括号 | `T-0d 08:00 小可: "…"` | 正常解析 |
| 系统事件 | `[T+0 16:34] [系统已通知监护人]` | role=system（T6 危机题关键事件）|
| 元注释 | `(T-5d至T-1d 无使用记录)` | 进 `session_events`，不算 turn |

role 归一：说话人含「小可」→ assistant；其余具体人名 → user；系统事件 → system。
timestamp 合成：固定基准日 2026-06-01（UTC+8）+ day_offset + 当日 HH:MM，可复现。

## gold 回填策略（归因的基石）

gold（「该想起哪句」）必须定位到具体 turn_id，Attribution F1 才能算。匹配分三级
（防误匹配，宁缺勿错）：

| gold 正文长度 | 策略 | 理由 |
|---|---|---|
| ≥4 字 | 前缀 12 字包含匹配 | 容忍引用略有截短 |
| 恰 3 字（「习惯了」）| 必须与某 turn **全文精确相等** | 包含匹配会误命中长句 |
| <3 字（「好」「嗯」）| **拒绝定位** | 单字确认句归因力为零；该把 gold 挂到信息承载句 |

多句 gold（distributed 题）：按 `//` 切分逐条定位，**任何一条失败即整体失败**进修题
清单——绝不部分成功地混过去。

## 特殊题型机制

| 标记 | 含义 | 判分含义 |
|---|---|---|
| `judgment_mode=negative_only` | 该遗忘型（S08 周报×3）：没有「该想起」只有「不该提」 | gold 为空合法，判分只查雷区（提及负面旧事即扣分）。注意对记忆后端归因弱，报告标注 |
| `history_all_write` | 触发本身是新会话开始 | 全部历史写入记忆，current 为空 |
| reclassify（补丁 op） | 题目设计意图就是当场（非笔误） | 移出记忆轨，归行为评测（题不浪费）|

## 红线门禁（自动跑，命中即拦截）

1. **泄 gold 扫描**：gold 句片段出现在 scene_context → 拦截（NoMemory 会作弊，区分度崩塌）
2. **当场题拦截**：gold 定位到 current_session → 拦截并写明两条修法
3. **补丁失配**：replace 的 old 串在字段里找不到 → `patch_failed`（飞书表更新后补丁
   漂移会被立刻发现，不会静默出错）

## 排查指南（出问题先看哪）

| 症状 | 先查 | 常见原因 |
|---|---|---|
| 某题没进 jsonl | `v01_memory_issues.csv` 搜题号 | 被某条门禁拦截，详情列写明原因 |
| `patch_failed` | 对比飞书表与 `raw/` 快照该题内容 | 飞书表改了，补丁的 old 串失配 → 重下 CSV + 更新补丁 |
| gold_not_found | gold 是否原样引用？是否单字？ | 印象改写 / 区间引用 / 确认音当 gold |
| 当场题误报 | 该题「根本目标」是否真是跨会话意图 | 是 → 补 A 类平移补丁；否 → B 类 reclassify |
| 某 turn 没解析 | 是否匹配上面 4 种格式 | 新格式变体 → 扩展正则 + 在本文档登记 |
| 题数对不上 | 149(jsonl) + 2(重标) + 46(B系列跳过) = 197 | 恒等式破了说明数据/补丁有变 |

## 与 harness 的衔接（已知缺口）

1. **`build_prompt` 还没消费 `current_session`**（38 题带当场上下文）——harness 升级
   后这些题的 prompt 才会带「当前会话」段。升级前跑这 38 题会丢当场信息。
2. **`expected_facts` 为空**：新表的「答对要点」是人话短语，NLI 判分需要完整命题句
   （hypothesis）。计划：LLM 批量产草稿 + 人工确认（Phase 2）。
3. 0-3 锚点已保留在 `rubric_anchors` 字段，供 judge 路径备用。
