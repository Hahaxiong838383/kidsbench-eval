# Adapter/Harness Tier1 命根实施计划

> 日期：2026-05-31
> 范围：`ADAPTER_ADJUSTMENT_DECISION.md` Tier1 命根 4 项 + Round2 必须项。仅 cc(adapter/harness) 侧，题库侧并行执行 `QUESTIONBANK_EXECUTION_PROMPT.md`。
> 方法：TDD（先写测试 RED → 实现 GREEN → 重构）+ 双门验证（Verify 目标达成 + Guard 208 测试/lint 不破）。
> 状态：**计划，待 3 个决策点定后开工**。

---

## 范围（Tier1 + Round2 必须）

| 编号 | 来源 | 内容 | 改哪 |
|------|------|------|------|
| **B-AF** | C 决策 | Attribution F1 scorer（单/分布式双口径，T_pred 空不计入） | harness/scorer |
| **C-NLI** | B 决策 | NLI 蕴含判定器（judge 独立于被测 LLM） | middleware + harness |
| **D-INJ** | A 决策 | 统一 LLM/embedding 注入器 + 锁定校验 | middleware + adapter |
| **A-FLUSH** | 混淆变量 | write→read 强制 flush gate | harness |
| **E-T6Q** | Round2 ③ | T6 control/scenario 双 query 三态判定 | harness |
| **F-EMB** | Round2 ⑤ | source_embedding 辅路激活（computed） | harness/scorer |
| **G-CTR** | 地基 | 契约最小改动（provenance_mode / current_timestamp 等 frozen 默认值） | contract |

---

## 依赖图

```
G-CTR(契约字段) ──┬──→ F-EMB(辅路需 provenance_mode)
                  └──→ (current_timestamp 透传)
A-FLUSH(独立) ────→ 可最先做
B-AF(Attribution F1) ──→ F-EMB(辅路补全)
C-NLI ──→ 需决策点①(judge 模型)
D-INJ ──→ 需决策点③(memoryos 处理)
E-T6Q ──→ 依赖题库出双 query（可 mock 先建逻辑）
```

---

## Phase 拆分（每 Phase 含 Verify + Guard 双门）

### Phase 0：契约地基 + flush gate（0.5d，无外部依赖，先做）
**目标**：契约可选字段就位 + flush 强同步，为后续 Phase 铺路。
- G-CTR：`contract/types.py`
  - `Memory` 末尾加 `provenance_mode: ProvenanceMode = "unknown"`（Literal native/wrapped/computed/fallback/unknown）
  - `Memory` 末尾加 `text_nature: TextNature = "unknown"`
  - `ReadOpts` 加 `current_timestamp: float | None = None`
  - `ConsolidateStats` 加 `consolidation_phase: Literal["write_time","explicit","unknown"] = "unknown"`
  - 模块加 `__schema_version__ = "2.0"`
- A-FLUSH：`harness/run_eval.py` evaluate_one——batch_write 后、read 前**强制调 flush() 并断言成功**（现有可能已调，强化为硬 gate + 失败即该题 error）
- **TDD**：先写 test_contract 新字段默认值测试 + test flush 时序测试（RED→GREEN）
- **Verify**：新字段默认值正确，flush 在 read 前必被调用
- **Guard**：208 测试零修改通过 + ruff

### Phase 1：Attribution F1（1d，C 决策核心）
**目标**：harness 用 Attribution F1 客观惩罚全量兜底，分布式题不冤枉。
- B-AF：`harness/scorer.py` 加 `attribution_f1(recalled_turn_ids, gold_turn_ids, fact_distribution)`
  - single：标准 P/R/F1；distributed：Recall 放松为"召回≥1 gold 即覆盖"，Precision 不放松
  - T_pred 空 → 返回 `{"f1": None, "counted": False}`（该题 attribution 不计入）
- evaluate_one：与现有 recall_score **并列**输出（不删旧的，保兼容），读题目 `fact_distribution`（默认 single）
- F-EMB：scorer 消费 `source_embedding`，cosine>0.85 反查 gold turn（系统无 turn_id 时），结果标 `provenance_mode=computed`
- **TDD**：先写测试——单事实 F1=1.0 / 全量兜底(T_pred=50,gold=1) F1≈0.039 / 分布式 Recall 放松 / T_pred 空不计入 / 辅路反查（RED→GREEN）
- **Verify**：4 个口径用例数值正确（含 gemini 给的 F1≈0.04 模拟）
- **Guard**：208 + 新测试通过

### Phase 2：NLI 内容验证（1d，B 决策核心）【阻塞于决策点①】
**目标**：判分剥离表述差异，只测语义信息保留。
- C-NLI：新 `middleware/nli_judge.py`——`entail(premise_texts, hypothesis) -> bool`
  - judge 模型**独立于被测 LLM**（被测锁 gemini-3-flash → judge 用决策点①选的模型）+ 温度0 + 固定 prompt
  - positive：判召回是否蕴含 expected hypothesis；negative：判是否蕴含互斥命题（乱猜）
- `harness/scorer.py`：judge 从 regex 升级为 NLI（保留 regex 作快速预筛/降级）
- 人工抽检：留 `nli_human_agreement` meta 指标接口
- **TDD**：先写测试——"布偶猫"蕴含"养了布偶猫"=true / "橘猫"蕴含"养了布偶猫"=false / 互斥 negative 判定（RED→GREEN，judge 用 mock）
- **Verify**：NLI 对 hypothesis/互斥 negative 判定准确率（小标注集）
- **Guard**：208 + 新测试

### Phase 3：统一注入 + 锁定校验（1d，A 决策）【阻塞于决策点③】
**目标**：强制三家用同一 LLM/embedding，运行时可校验。
- D-INJ：`middleware` 加统一注入器（harness init 时构造统一 LLMClient/EmbeddingService 注入三家）
  - `MemoryAdapter` 加非抽象 `get_injected_providers() -> dict[str,str]`（默认 {}）
  - `preflight`：对 internal_llm/embed 校验 swap_supported + config_key + actual 采样
  - memoryos swap=False 处理 → 决策点③
- **TDD**：先写测试——三家注入同一 client、get_injected_providers 返回一致、不一致系统标 Model-Locked（RED→GREEN）
- **Verify**：三家运行时确实用注入的统一 client
- **Guard**：208 + 新测试 + 三家真实 smoke 不破分

### Phase 4：T6 双 query 三态（0.5d，Round2 ③）【依赖题库双 query，可 mock 先建】
**目标**：T6 安全红线干净区分 pass/警觉失败/被吞。
- E-T6Q：harness 跑 control_query + scenario_query，三态判定（都召回=pass / 仅 control=警觉失败 / 都召不回=被吞）
- **TDD**：mock T6 题（含双 query）测三态逻辑（RED→GREEN）
- **Verify**：三态判定正确
- **Guard**：208 + 新测试

---

## 需要川哥定的 3 个决策点（开工前）

| # | 决策 | 选项 | cc 倾向 |
|---|------|------|---------|
| **①** | NLI judge 用什么模型（必须独立于被测 gemini-3-flash） | a) GPT-4o-mini（便宜稳）b) Qwen（国产对齐）c) gemini-3-pro（同族不同档，但仍共享 Google 偏见，不推荐） | **a) GPT-4o-mini**，与被测 Google 系彻底解耦，judge 成本低 |
| **②** | Attribution F1 与现有 recall_score 关系 | a) 并列（保留 recall 兼容旧 run）b) 替换 | **a) 并列**，旧 run 可比 + 平滑迁移 |
| **③** | memoryos swap=False 怎么处理 | a) Monkey Patch 强注入统一 client b) 标 Model-Locked 隔离对比 | **a) 先试 Monkey Patch**，失败再降 b（Model-Locked 是兜底） |

---

## 总工作量 & 风险

- **工作量**：Phase 0(0.5d) + 1(1d) + 2(1d) + 3(1d) + 4(0.5d) ≈ **4 天**（决策点定后）
- **可并行**：Phase 0 独立先做；Phase 1 不阻塞；Phase 2/3 等决策点；Phase 4 等题库
- **风险**：
  - NLI judge 成本/可用性（决策点①未定则 Phase 2 阻塞）
  - 题库 fact_distribution 标注未就绪 → Phase 1 先默认 single 跑通，待题库标注补全
  - 题库双 query 未就绪 → Phase 4 mock 先建逻辑
  - memoryos Monkey Patch 脆弱（已知读 `_turn_index` 私有字段，注入也可能碰内部）→ 决策点③兜底 Model-Locked
- **不变量**：每 Phase 必过 Guard（208 测试 + ruff），契约改动全 frozen 末尾默认值零破坏
