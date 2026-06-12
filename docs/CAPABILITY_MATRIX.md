# Adapter 能力对照矩阵

> **生成方式**：本表由 `python -m kidsbench.tools.capability_matrix` 自动生成（待实现）。手动维护时确保跟 `get_capability_profile()` 一致。
>
> **数据来源**：每个 Adapter 的 `get_capability_profile()` 返回值。
>
> **更新策略**：新增 Adapter 或修改 Adapter 实现后必须重新生成。

## 当前状态

只有三个基线 Adapter 完成。Wave 1 三家（Mem0 / MemoryOS / Graphiti）待接入后填表。

## 能力对照（11 个 STANDARD_FEATURES）

| Adapter | physical<br>clear | turn_id<br>traceback | cognitive<br>filter | score<br>normalized | concurrent<br>safe | cost<br>accounting | embedding<br>export | flush<br>blocking | consolidate<br>explicit | batch_write<br>native | write_sem<br>sync |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **NoMemory** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **FullHistory** | ✅ native | ✅ native | ❌ | ⚠️ declared | ⚠️ declared | ❌ | ❌ | ✅ native | ❌ | ⚠️ declared | ✅ native |
| **Oracle** | ✅ native | ✅ native | 🔵 computed | ⚠️ declared | ⚠️ declared | ❌ | ❌ | ✅ native | ❌ | ⚠️ declared | ✅ native |
| **Mem0** | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| **MemoryOS** | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| **Graphiti** | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| **Memobase** | ✅ native | 🟢 wrapped¹ | ⚠️ declared | 🔵 computed | ✅ native | ⚠️ declared | ⚠️ declared | ✅ native | ⚠️ declared | 🟢 wrapped | 🟢 wrapped² |
| **MemMachine** | ✅ native | ✅ native | ⚠️ declared | 🔵 computed⁶ | 🟢 wrapped⁷ | ⚠️ declared | ⚠️ declared | ✅ native | ⚠️ declared | 🟢 wrapped | ✅ native |
| **Cognee** | ✅ native | 🟢 wrapped³ | ⚠️ declared | 🔵 computed | ⚠️ declared⁴ | 🟡 simulated | ⚠️ declared | ✅ native | ✅ native | 🟢 wrapped | ⚠️ declared⁵ |

> ¹ Memobase 溯源仅 date-level：画像是 LLM merge 派生物，同日多 turn 不能唯一绑定 turn_id（codex 对抗审 P0 钉死，如实标 wrapped declared-weak）。
> ² Memobase write 入 buffer，画像在 flush(sync) 才就位 → write_semantic_sync 非真同步，harness 必须 write→flush→read。
> ³ Cognee 溯源最弱：GRAPH_COMPLETION 合成文本无源引用，仅 node_set=turn 批次标记。
> ⁴ Cognee 无 dataset 局部隔离（prune 全局）→ 评测协议逐题全清重建保隔离，并发不安全。
> ⁵ Cognee write 仅入库，知识图谱在 consolidate(cognify) 才建好 → harness 必须 write→consolidate→read；虚拟时钟无注入口（lineage declared）。
> ⁶ MemMachine LTM 自带 score（实测 0.56），STM 无 score 作兜底召回排其后——非全局归一（codex 对抗审 P1 降级 native→computed）。
> ⁷ MemMachine server 端 project_id 隔离，但 adapter 共享 requests.Session + _seen 非线程安全；评测串行单进程成立（codex 对抗审 P1 降级 native→wrapped）。
>
> **codex 对抗审采纳记录（adapter 代码，2026-06-12）**：MemMachine `_merge_episodes` STM 不再塞 score=0 全局排序（改 LTM 按 score + STM 接后）/ `_post` JSON 解析失败走 AdapterError / score_normalized+concurrent_safe 两项能力降级 / Cognee 查重键 content-hash→turn_id（防同文本不同 turn 误去重）/ 三家进程内状态约束写进 docstring。未采纳：monkey patch 作用域收窄、uid 映射持久化（评测协议串行单进程不踩，留生产 backlog）。

**图例**：
- ✅ `native` — 候选系统原生支持
- 🟢 `wrapped` — Adapter 用真数据包装
- 🔵 `computed` — Adapter 用真数据计算
- 🟡 `simulated` — 经验估算（带误差范围）
- ⚠️ `declared` — 明确声明不支持
- ❌ `unsupported` — 物理无法实现

## Lane 适配性

| Adapter | A1 (Qwen) | A2 (GPT-4) | A3 (本地 7B) | B (自由) | C (无 LLM) |
|---|---|---|---|---|---|
| NoMemory | ✅ | ✅ | ✅ | ✅ | ✅ |
| FullHistory | ✅ | ✅ | ✅ | ✅ | ✅ |
| Oracle | ✅ | ✅ | ✅ | ✅ | ✅ |
| Mem0 | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| MemoryOS | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |
| Graphiti | _待填_ | _待填_ | _待填_ | _待填_ | _待填_ |

## 范式覆盖（4 轴 + cognitive）

| Adapter | representation | retrieval | write_policy | controller | cognitive |
|---|---|---|---|---|---|
| NoMemory | none | none | none | none | — |
| FullHistory | raw_text | none | append_only | none | episodic |
| Oracle | raw_text | gold_lookup | append_only | oracle | episodic+semantic+procedural |
| Mem0 | vector+entity | hybrid_rerank | reactive | rule | semantic |
| MemoryOS | raw+vector+三层 | tiered_consolidation | consolidation | os_inspired | episodic+semantic |
| Graphiti | temporal_kg | 16_recipes_multi_hop | event_chain | rule(+temporal) | episodic+semantic+procedural |
