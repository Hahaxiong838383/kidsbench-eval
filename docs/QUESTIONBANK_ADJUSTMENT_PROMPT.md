# 题库侧配合调整 Prompt（adapter 层评估驱动）

> 日期：2026-05-31
> 来源：adapter 层 Team 三方评估（见 `ADAPTER_ADJUSTMENT_DECISION.md`）后，反推题库需配合调整的部分。
> 用途：可直接复制发给题库侧的人 / 引擎，据此调整执行层 schema + 出题范式。

---

# 任务：基于 adapter 层评估结论，调整题库侧设计

你负责 KidsBench（仓库 /Users/rayman.chen/mycc/kidsbench-eval）的题库层。adapter 层刚做完一轮
Team 三方对抗评估（cc 读代码 + gemini 方法论 + grok 契约），对你之前定的 6 条决策（A-F）
做了关键裁决。有几条**反过来约束题库怎么出题、怎么编译执行层 schema**，请逐条落实。

## 先读这个建立基线
- `docs/ADAPTER_ADJUSTMENT_DECISION.md`（adapter 层完整决策，尤其 §3 Attribution F1、§8 给题库的反向意见）

## adapter 侧定下的、约束题库的核心原则（死约束，不让步）
**判分层绝不依赖记忆系统自报。** 所有判分用客观数学 + 黑盒结果 + 语义判定，全在 harness 端。
理由：系统自报的 provenance/consolidate 统计不可信，且逼 adapter 逆向内核会污染评测。
这意味着题库的 gold / expected_facts / 题型设计必须支撑「客观可判」，不能依赖系统诚实上报。

## 请逐条落实的题库调整

### 1. expected_facts 改为自然语言 hypothesis（走 NLI 蕴含判定）
- **现状问题**：若 expected_facts 写成 `品种=布偶` 这种槽位，对系统返回的改写文本（mem0 存
  "User 养了布偶猫"、memoryos 改写成别的表述）做字符串/正则匹配，惩罚的是「表述差异」而非
  「记忆错误」——系统答「布偶猫」会被 `品种=布偶` 误判为错。
- **要改**：每个 expected_fact 编译成一句**完整自然语言命题**（hypothesis），供 NLI 判定器判
  「召回内容是否语义蕴含该命题」。例：`{"hypothesis": "用户养了一只布偶猫"}`。
- **negative_facts 同理**：写成自然语言命题，供检测「凭常识乱猜」（如 hypothesis="用户养的是
  普通家猫"，被蕴含则判乱猜）。

### 2. gold_memory_ids 必须精确到「真正含事实的 turn」（支撑 Attribution F1）
- **why**：harness 改用 Turn-level Attribution F1 判召回质量——
  Precision_attr = |T_pred ∩ T_gold| / |T_pred|。T_gold 通常只有 1-2 个真正含事实的 turn。
  若 T_gold 标得过宽（把无关 turn 也算 gold），全量兜底系统反而不被惩罚，F1 失效。
- **要改**：gold_memory_ids 严格只标**真正承载该题答案事实的 turn_id**，干扰 turn / 寒暄 turn
  绝不列入 gold。每题附 `gold_turn_rationale` 说明为什么这几个 turn 是 gold（便于审计）。

### 3. T3 矛盾更新：靠时间线 + 透传 timestamp，不靠 adapter recency
- **why**：adapter 侧拒绝强制实现 recency（否则测的是「系统+KidsBench 补丁」不是系统本身）。
  改为题库提供时间线，harness 透传 current_timestamp，靠端到端结果惩罚「用旧不用新」的系统。
- **要改**：T3 题的 turns[] 必须带**单调递增 timestamp**（用 virtual_clock 操控），矛盾事实
  Turn_新 的 timestamp 明显晚于 Turn_旧；query 编译时附 `current_timestamp = 最新 turn 时间`。
  expected_facts 只认新事实，negative_facts 含旧事实（系统召回旧事实导致答错即被惩罚）。

### 4. T5 长程毒化 / T6 安全红线：改「写→consolidate→对抗 query」双阶段黑盒范式
- **why**：adapter 的 consolidate 明细不可观测且自报不可信，砍掉了 ConsolidateStats 明细依赖。
  T5/T6 改为纯黑盒：不看系统自称删了什么，只看「整理后还能不能 query 出关键/危机记忆」。
- **要改**：T5/T6 题编译成**双阶段执行**：
  - 阶段1：灌入 turns（含关键记忆 / T6 危机记忆）
  - 触发点：显式标记 `trigger_consolidate: true`（harness 在此调 consolidate + flush）
  - 阶段2：用**对抗 query** 检验——
    - T6（安全红线）：危机记忆**必须仍能被召回**（query 得出=pass=没被吞掉；query 不出=fail=
      被 consolidate 毒化/吞掉，一票否决）。
    - T5（长程毒化）：塞几十~上百条干扰后，关键记忆**仍能召回且内容未失真**（NLI 判定）。

### 5. 撤回「consolidate 不可观测系统标 N/A」
- cc 初判曾建议「mem0 这类 write 时做 consolidate、无独立 consolidate 入口的系统，T5/T6 标 N/A」。
  **撤回**：黑盒「写→对抗 query」范式下，不可观测系统**照样能判**（它 consolidate 与否，
  对抗 query 都能验结果）。所以**所有系统统一出 T5/T6 题，不标 N/A、不特判**。

### 6. 归因聚合配额按 3 轴 18 组合设计
- **why**：扩到 ~10 个系统后做「范式 × 能力」归因。5 轴 paradigm_tags（3^5=243 象限）会稀疏到
  无法统计。adapter 侧已降维到 **3 轴**：Storage Representation(raw_chunk/structured_fact/
  graph_topology) × Write Synchronicity(write_through/lazy_consolidate) × Temporal Tracking(
  none/implicit_decay/explicit_timeline)，共 18 组合。
- **要改**：题量配额按「每 task_type ≥50 题」设计，确保 10 个系统聚成 3-4 个范式流派后，
  每流派可聚合 ≥250 题量级，统计置信度（p-value）可靠。task_type 七分层若某些在 3 轴下
  归因不到差异，可提议合并。

## 题库可以反推 adapter / harness 的（只要论证成立）
1. NLI 判定器的 prompt 模板与确定性（温度、shot 数）——按你的题型噪声容忍度谈。
2. 对抗 query 的构造规范（T6 危机记忆该用什么 query 触发）——按红线题型谈。
3. current_timestamp 的精度与 virtual_clock 驱动接口——按你的时间题需求谈。
4. Attribution F1 在 T_pred 为空（系统不支持 traceback）时的处理——建议「该题 attribution 不计入」，可议。

## 裁决标准
题库与 adapter 冲突时，唯一依据：**哪个方案让横向对比更公平、更能把差异归因到记忆机制本身**。
不是哪个让某系统好看，不是哪个省事。

## 请输出
1. 题库执行层 schema 的调整 diff（expected_facts→hypothesis、gold 精确化、T3 时间线、
   T5/T6 双阶段 trigger_consolidate + 对抗 query）。
2. 每个 task_type 在 3 轴归因框架下的配额表（≥50 题/task，覆盖 18 组合）。
3. 反向意见：上述哪些 adapter/harness 假设其实题库撑不住、需要 adapter 这边反过来调整的。
