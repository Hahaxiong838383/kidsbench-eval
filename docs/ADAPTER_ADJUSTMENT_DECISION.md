# Adapter 层调整决策（题库 6 决策驱动）· Team 三方综合

> 日期：2026-05-31
> Team：cc(读全部代码建基线 + A-F 初判) × gemini-3.5-flash high(方法论对抗) × grok-build(契约/类型严谨 + 向后兼容)
> 触发：题库侧经三引擎对抗审定下 6 条决策（A-F），直接约束 adapter 契约，需评估 adapter 是否调整。
> 状态：**仅决策记录，未改代码**。

---

## 0. 裁决标准（唯一）

题库与 adapter 冲突时，以**「哪个方案让横向对比更公平、更能把差异归因到记忆机制本身」**为唯一依据——
不是哪个让某系统好看，不是哪个省事。项目目标：公平横向对比 N 个记忆系统（3→~10 GitHub 高星），反推自研设计原则。

---

## 1. 灵魂结论：判分客观化 + 可观测诊断化（两线分离）

Team 三方最大的收敛（也是 gemini vs grok 张力的裁决）：

- **判分层绝不依赖 adapter 自报**（gemini「不信自报」是最高原则）
  → 用客观数学（Attribution F1）+ 黑盒结果（对抗 query）+ NLI 语义判定。全在 harness。
- **契约字段只用于白盒可观测/归因**（grok 字段设计）
  → 可选、向后兼容（末尾默认值 unknown）、零成本，但**不进判分关键路径**。

效果：既客观公平（判分不被系统忽悠），又可解释归因（白盒看得见机制）。契约主体稳定，扩 10 家接入成本不变。

---

## 2. A–F 逐条裁决（cc 初判 → gemini/grok 修正 → 最终）

| 决策 | cc 初判 | gemini 修正 | grok 修正 | 最终裁决 | 改哪层 |
|------|---------|------------|----------|---------|--------|
| **C provenance** | 加 provenance_mode + 打折 | **免打折**：harness 算 Turn-level **Attribution F1**，全量兜底 precision 自动暴跌(F1≈0.04) | 加 provenance_mode 字段(frozen 兼容) | **判分靠 F1**（不依赖自报）+ provenance_mode 仅白盒展示 | harness（判分）+ 可选字段 |
| **A 统一锁定** | 标准注入接口 + 校验 | Harness **强制注入** client + Monkey Patch + 硬锁系统隔离 Model-Locked | Dependency 加 config_key/actual_model + `get_injected_providers()` 运行时采样校验 | **采纳两者**：中间件注入器 + 运行时锁定校验 | 中间件 + adapter 注入点 |
| **B 内容验证** | 加 text_kind + 槽位匹配 | **砍标记**（过度工程）；改 **NLI 蕴含判定** | 加 text_nature(verbatim/extracted/synthesized) | **两线分离**：判分用 NLI(gemini) + text_nature 留作归因标签(grok，非判分依据) | harness（判分）+ 可选字段 |
| **D consolidate** | 加 ConsolidateStats 明细 | **砍明细**；T5/T6 黑盒「写→整理→对抗 query」 | 加 ConsolidateChange + middleware before/after 快照兜底 | **判分用黑盒**(gemini)；changes 降为可选诊断(grok middleware 快照≈黑盒) | harness（题型）+ 可选字段 |
| **E 时间语义** | 反对强制 adapter recency | **强烈同意 cc**；透传 timestamp + 端到端惩罚 | — | cc 反向成立：ReadOpts 透传 current_timestamp，harness 结果惩罚 | ReadOpts + harness |
| **F 扩展性** | 受控词表 + 模板 | **5 轴→3 轴**(243 象限稀疏灾难→18 组合) | cognitive_type 也需受控词表 | 采纳 3 主轴枚举 + cognitive 受控 | 契约(paradigm_tags 枚举) |

### F 的 3 轴受控词表（gemini，扩 10 家归因聚合用）
| 轴 | 受控枚举 | 决定什么 |
|----|---------|---------|
| Storage Representation | `raw_chunk` / `structured_fact` / `graph_topology` | 信息密度与关联度 |
| Write Synchronicity | `write_through` / `lazy_consolidate` | 抗长程干扰 + 即时召回 |
| Temporal Tracking | `none` / `implicit_decay` / `explicit_timeline` | 矛盾更新 + 时序记忆能力 |

3×2×3 = 18 组合，10 家天然聚成 3-4 流派，每流派可聚合 ≥250 题量级，统计置信度可靠。

---

## 3. C 的核心方案：Turn-level Attribution F1（gemini，免打折系数）

不改 recall 算法本身，harness 端并列引入归属 F1：
- T_pred = 系统本次召回声明的全部 turn 集合（∪ 各 Memory.source_turn_ids）
- T_gold = 本题真正含事实的 gold turn 集合
- Precision_attr = |T_pred ∩ T_gold| / |T_pred|
- Recall_attr = |T_pred ∩ T_gold| / |T_gold|
- F1_attr = 2·P·R / (P+R)

模拟：
- 精确系统(mem0)：T_pred={3}, gold={3} → F1=1.0
- 全量兜底(memoryos)：T_pred={1..50}, gold={3} → Precision=0.02 → **F1≈0.039**

**纯数学惩罚「全量兜底作弊」，无需人为打折系数。**
cc 补充边界：T_pred 为空（不支持 traceback）时 P=0/0，定义为「该题 attribution 不计入 / 单列 unsupported」，避免除零。

---

## 4. gemini 补的 2 个混淆变量（cc/grok 都漏）

1. **异步整理赛跑效应**：write 后立即 read，异步 consolidation 没跑完 → 测的是 CPU 调度不是记忆机制。
   → **flush 必须强同步阻塞硬 gate**，harness read 前强制 `flush()`。
2. **embedding 域外偏见**：即便锁同一 embedding，各系统分块策略不同致向量分布偏移。
   → capability 声明 optimal chunk size（cc 补充：**只记录透明化，不允许 adapter 私自微调**，否则又成变量）。

---

## 5. grok 纠正 cc 2 处 + 新增契约盲点

### 纠正（采纳）
1. **graphiti 不是「纯图回溯」**：实际 `sidecar primary + query_provenance + cypher 兜底` 三重保险(read:203 + 419-444)，比 cc 描述更鲁棒、比 memoryos 全量兜底更诚实。cc 低估了它。
2. **memoryos fallback 更脏**：直接读 `_sidecar._turn_index` 私有字段(:575)，sidecar 重构即炸。是脆弱实现，不只是「无标记」。

### 新增高价值契约盲点（采纳）
| # | 问题 | 处置 |
|---|------|------|
| consolidate 语义不一致 | mem0=「已固化」声明 vs memoryos/graphiti=「请执行」，metrics 不可比 | ConsolidateStats 加 `consolidation_phase`(write_time/explicit) |
| capability runtime 脱钩 | 自报 wrapped 实际 fallback，无观测降级 | harness 跑完观测实际行为，自报与实际不符则降级（落地 gemini「不信自报」） |
| source_embedding 半成品 | 字段存在但 scorer 完全不消费，有的 adapter 给有的 None，公平已受损 | 决定：激活辅路 or 删字段，不留半成品 |
| 无 schema 版本 | frozen dataclass 无 `__schema_version__`，长期多轮 runs jsonl 无法可靠反序列化对比 | 扩 10 家前加 |
| cognitive_type 无受控词表 | 字符串匹配拼写漂移 | 并入 F 受控词表(Episodic/Semantic/Procedural) |
| adapter 非 frozen | name/paradigm_tags 类属性可被实例改，违反不可变原则 | 小改 |

---

## 6. 最终调整清单（五层）

**① 改 ABC 契约**
- paradigm_tags → 受控 3 轴枚举
- ReadOpts 加 current_timestamp 透传（E）
- flush 强化为强同步硬 gate（gemini 赛跑防范）
- 加 __schema_version__（grok）
- ConsolidateStats 加 consolidation_phase（grok，解决 mem0 no-op 不可比）

**② 加可选诊断字段（grok，白盒用，默认 unknown 向后兼容，不判分）**
- Memory.provenance_mode / text_nature
- ConsolidateStats.changes（adapter 报不了则空，middleware 快照兜底）

**③ 加中间件**
- 统一 LLM/embedding 注入器 + get_injected_providers() 运行时锁定校验（A）
- NLI 蕴含判定器（B）
- Attribution F1 scorer（C 核心）
- capability runtime 观测器（自报与实际不符降级，grok #3）

**④ 改 harness（判分核心，全部不靠自报）**
- recall → Attribution F1（C）
- 判分加 NLI 内容验证（B）
- T5/T6 黑盒双阶段 read 编排（D）
- write→read 强制 flush gate

**⑤ 改各 adapter**
- 统一注入点；memoryos swap=False 改真（Monkey Patch / 标 Model-Locked）；修 _turn_index 私有字段访问

---

## 7. 优先级（gemini Tier + cc/grok 调整）

- **Tier 1 命根**（不做无公信力）：C Attribution F1 / A 强制注入校验 / B NLI / flush 强同步 gate
- **Tier 2**（扩 10 家前必做）：F 3 轴受控 / E 透传 timestamp / __schema_version__ / consolidation_phase
- **Tier 3 可选诊断**（白盒可观测，非判分依赖）：provenance_mode / text_nature / ConsolidateStats.changes

---

## 8. 给题库侧的反向意见（对照「可谈」清单）

1. **B 槽位格式**（可谈⑤）：放弃字符串/正则，expected_facts 写成**自然语言 hypothesis**，用 NLI 语义蕴含判定——`品种=布偶` 对系统答「布偶猫」应判对。
2. **E 时间表达**（可谈②）：别用题库强压 adapter recency，T3 靠透传 timestamp + 端到端结果惩罚。
3. **D 颗粒度**（可谈⑥）：ConsolidateStats 明细不要了，T5/T6 改黑盒结果验证，题库按**写→consolidate→对抗 query 双阶段 read** 出题。
4. **task_type 前置**（可谈①）：撤回 cc 初判的「不可观测系统标 N/A」——黑盒方案下不可观测系统**仍可判**，不需 N/A。
5. **F 配额**：归因聚合按 3 轴 18 组合设计，每 task ≥50 题，聚合到 3-4 流派 ≥250 题量级。

---

## 附：三方贡献
- **cc**：读全部代码建基线（contract 三件 + 三家 adapter + scorer + run_eval），A-F 初判，张力裁决。
- **gemini-3.5 high**：砍过度工程（B text_kind / D 明细），Attribution F1 数学解，NLI 判定，黑盒 T6，5→3 轴，2 个混淆变量。
- **grok-build**：契约字段 frozen 向后兼容设计，纠正 cc 2 处（graphiti 三重保险 / memoryos 私有字段），10 个契约盲点。
