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
