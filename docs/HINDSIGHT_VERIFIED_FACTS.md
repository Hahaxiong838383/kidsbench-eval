# Hindsight 0.8.1 核实事实（Phase 0 产出）

> 日期：2026-06-10 | 版本锁定：hindsight-all **0.8.1**（.venv-hindsight）
> 方法：源码扫描（2 个 Explore agent，clone 自 vectorize-io/hindsight）+ 本机实测（embedded pg0 真跑 2 轮）
> 实测脚本：`scripts/phase0_hindsight_verify.py`（可重跑复核任何一行）
> 实测日志：/tmp/kb_phase0_run2.log + /tmp/kb_phase0_meta.log

## 红绿灯总表（10 核实点 + 2 附加）

| # | 核实点 | 结果 | 证据（源码 + 实测）|
|---|--------|------|------|
| 4 | **LLM 注入真生效**【一票否决】 | ✅ | 构造参数 `llm_base_url` 直传 AsyncOpenAI（openai_compatible_llm.py:353）；实测：`gemini-3-flash-preview`（仅存在于我们 proxy 的模型名）retain 成功抽 fact ⟹ 流量必打统一 endpoint |
| 5 | **中文 embedding/reranker 替换**【一票否决】 | ✅ | embedding 默认 `bge-small-en-v1.5`（英文！），env 一行换 `bge-small-zh-v1.5` **实测生效**（中文召回命中）；reranker 默认 `ms-marco`（英文），源码确认可换 `BAAI/bge-reranker-v2-m3`（config.py:295-320），换后质量实测留 Phase 1.5 |
| 9 | **reflect 纯读性**【一票否决】 | ✅ | 源码：reflect_async 全程零 INSERT/UPDATE（memory_engine.py:8135-8629），agent 工具全只读；mental models 只在 consolidation 写入。实测无记忆暴涨（1→2 为 query 措辞不同导致召回集不同，非写库）。**rerun_rounds 状态机风险解除** |
| 1 | retain 同步性 | ✅ | 默认 `async_=false` 直接 await 抽取完成才返回（memory_engine.py:3498）；无批处理阈值；实测 latency 24.7s（同步等待 LLM 抽取）→ flush 可轻量实现 |
| 2 | 召回溯源字段 | ✅ | 返回 document_id / chunk_id / **metadata 完整透传**（实测 `turn_id` 原样回）/ fact_type / occurred_start / mentioned_at → **turn_id 走 wrapped（同 mem0 级），sidecar 仅兜底** |
| 3 | embedded 存储形态 | ✅ | pg0 = 内嵌 PostgreSQL（自包含 Python 包，~/.pg0/instances/，**无外部服务**）；可经 `HINDSIGHT_API_DATABASE_URL` 切外部 PG。实测首启 100s（含下载），二次启动快 |
| 6 | token usage 计量 | ✅ | retain 实测返回 `{input:3150, output:210, total:7026}`；reflect 实测 client 响应 usage 暴露=True（比源码预判好）→ **成本计量原生可得，无需 AOP 拦截** |
| 7 | clear 物理级联 | ✅ | delete_bank 六步级联（documents→memory_units→invalidated→entities→banks→DROP per-bank HNSW 索引），schema 全 FK CASCADE（models.py:62-300）。实测：删 bank_a 后 recall=0 残留，bank_b 不受伤（交叉隔离过）|
| 8 | virtual_clock 对接 | ✅ | recall 接受 `query_timestamp`（http.py:3493）→ question_date → recency 计算（reranking.py:100-108 用传入时刻非硬编码 now）。实测参数被接受。**正好对接 ReadOpts.current_timestamp** |
| 10 | stats/deps 映射 | ✅ | delete_bank 返回 `{memory_units_deleted, entities_deleted, ...}` 计数；HTTP API 全枚举（http.py）→ get_stats/get_dependencies 映射路径清晰 |
| +1 | metadata 透传（附加实测）| ✅ | retain 传 `{"turn_id":"t_042","session_id":"s9"}` → recall 原样返回 |
| +2 | retain 幂等性（附加实测）| ⚠️ **非幂等** | 同内容写 2 次召回 2 条（gpt-5.5 预判命中）→ **Phase 2 必做 adapter 层 turn_id 写前查重**（防 retry 中间件重试造成重复记忆污染） |

**结论：10/10 全绿，3 个一票否决门全过。1 个 ⚠️（非幂等）有明确修法，转 Phase 2 必做项。**

## 实测出的重要工程事实（方案没预料到的）

1. **reflect 慢**：实测 78.4s/次（gemini-3-flash 经 proxy，多轮 agent 工具循环）。跑批影响：124 题 × reflect 模式 ≈ 2.7h+（还不算重试）。评测时序预算要按这个量级排。
2. **retain 也不便宜**：24.7s/turn + 7026 tokens（中文单句）。124 题多 turn 灌入的时间/token 预算要先估。
3. **fact_type 实际枚举**：`world / experience / opinion / observation`（与 README 的"3 类记忆"表述不同，observation 来自 consolidation）→ cognitive 映射表要按实际 4 类设计。
4. **中文抽取质量好**：「我家的布偶猫叫团子，特别喜欢吃冻干三文鱼」→ 抽出「用户的布偶猫名字叫团子，特别喜欢吃冻干三文鱼 | Involving: 用户, 团子」，实体关系正确。
5. server stop 后有 aiohttp unclosed session 警告（无害，上游库清理问题，adapter teardown 注意）。

## Phase 1.5 A/B 实测补充（2026-06-11）

### reranker 中文 A/B（死结验证，gemini 预判实锤）
同 bank 5 条中文记忆（1 目标 + 4 同领域"猫"干扰），query「我自己养的猫叫什么名字」：
| 配置 | top1 | 结论 |
|---|---|---|
| 默认 `ms-marco`（英文）| ❌ t_005（美术作业画的猫，字面匹配）目标被压到 #4 | 英文 reranker 中文语义失效，**必须换** |
| `BAAI/bge-reranker-v2-m3` + 关 auto-consolidation | ✅ t_001 第一，前 5 零英文污染，排序语义合理 | **死结解除** |

### ⚠️ 新发现：auto-consolidation 默认开启 + 产英文 observation
- `DEFAULT_ENABLE_AUTO_CONSOLIDATION = True`（config.py:854）：**retain 后自动触发后台 consolidation**，用英文 prompt 生成英文 observations（**不带原 metadata/turn_id**）——首轮 A/B 中污染了召回（top1 是英文翻译版记忆）
- 这是「异步整理赛跑」的 Hindsight 实例：评测期间后台不受控写入会破坏可比性 + 溯源
- **评测标准配置（锁定）**：`HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION=false`，consolidation 只走显式 API `POST /v1/default/banks/{bank_id}/consolidate`（http.py:5936）——正好映射契约 consolidate 方法，harness 完全掌控时序
- 遗留观察：显式 consolidate 产出的 observation 仍可能是英文（consolidation/prompts.py 英文 prompt）→ T5/consolidate 相关题测试时注意，记 capability notes

### 评测标准 env（Phase 2 adapter 固化这组配置）
```
HINDSIGHT_API_EMBEDDINGS_PROVIDER=local
HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-zh-v1.5
HINDSIGHT_API_RERANKER_PROVIDER=local
HINDSIGHT_API_RERANKER_LOCAL_MODEL=BAAI/bge-reranker-v2-m3
HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION=false
```

## Phase 3 smoke 实测（2026-06-11，runs/hindsight_smoke/hs_smoke_2）

6 题 × 双模式真跑（统一 gemini-3-flash 注入），**preliminary（N=6 不下范式结论）**：

| adapter | correct | write_tok | read_tok | write_s | read_s |
|---|---|---|---|---|---|
| hindsight-recall | 6/6 | 90,705 | **0** | 357s | **1.3s** |
| hindsight-reflect | 6/6 | 83,742 | **43,240** | 330s | **47.1s** |

- **范式旋钮实证**：同一系统、同样写入，read 成本结构完全不同（recall 零 LLM vs reflect 43k tokens）——早/晚绑定两个 Pareto 数据点真实拉开
- smoke 6 题双模式都触顶（同当年 memoryos/graphiti），区分度需 124 题
- **reflect 的 thought_signature 事故与修复**：reflect agent 多轮 tool call 撞 gemini-3 经 OpenAI 兼容 proxy 丢 `thought_signature`（Vertex 硬要求）→ HTTP 400×6 → 全 error。**修复**：reflect stage 族内降级 `gemini-2.5-flash`（per-stage env `HINDSIGHT_API_REFLECT_LLM_*`，同 proxy），实测 0 错误 + 正确中文合成 + reflect 7s/题（比 3-flash 78s 还快）。retain 仍统一 gemini-3-flash。capability 标注：reflect 内部 LLM 与统一注入不同族版本（成本照常计量，符合「锁裁判+价格表」新框架）
- 根因归属：**proxy wrapper 对 gemini-3 多轮 function call 支持不完整**（非 Hindsight/adapter 缺陷）；mem0/memoryos/graphiti 内部无多轮 agent 故从未触发。修 wrapper 透传 thought_signature 是长期正解（backlog）

## 给 Phase 1/1.5/2 的输入

- Phase 1：pg0 即可（无需 QNAP Postgres！比原方案更轻）；bge-reranker-v2-m3 权重下载
- Phase 1.5 三死结瘦身为**一死结**：只剩 reranker 中文替换质量实测（token 拦截原生解决 / virtual_clock 原生对接，两个死结消失）
- Phase 2 新增必做：turn_id 写前查重（幂等）；fact_type 4 类映射；teardown 显式 close
