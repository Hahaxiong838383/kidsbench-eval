# LangGraph/LangMem 接入 KidsBench Adapter 方案

> 日期：2026-06-09
> 目标：让生产自研 LangGraph+LangMem 记忆系统，像 mem0/memoryos/graphiti 一样接入 adapter，作为评测的「生产水位线」基线。
> 约束承接：①验收物 = 需求格子 × Pareto 支配生产基线的可借鉴机制清单 ②范式新框架（read 用 LLM 不封杀，计成本+标 paradigm）③第一步准入红线（隔离/确定性/物理清场/强同步 flush）
> ⚠️ 生产系统真实形态未知，本 spec 给「生产侧要提供什么接口」+「KidsBench 侧怎么消费」；标 `[需核实]` 处待川哥提供事实

---

## 0. 核心架构决策：两层封装（窄协议解耦）

三家（mem0/memoryos/graphiti）是 pip 装的稳定第三方 SDK，adapter 直接耦合其 API。生产 LangGraph 系统**形态未知、业务耦合、会演进**——直接让 KidsBench adapter 耦合生产内部太脆。

→ **中间放一个窄协议 `EvalMemoryBackend`**：

```
KidsBench harness
   ↓ 调 7 契约方法
LangGraphAdapter(MemoryAdapter)         ← cc 实现，和三家同构（sidecar/embedding/注入/capability）
   ↓ 调窄协议(剥离 agent 推理的纯记忆操作)
EvalMemoryBackend (Protocol)            ← 川哥团队实现，这就是「langgraph 要提供的接口」
   ↓ 包装
生产 LangGraph+LangMem 记忆子系统        ← 未知形态,被协议隔离
```

**好处**：①川哥团队只实现一个窄协议，不碰 KidsBench 内部 ②生产形态变了只改 backend 实现，adapter 不动 ③协议 = 前面迭代的「接口需求清单」的代码化，清晰可测 ④和三家完全同构的 adapter 层，公平接入有保证。

---

## 1. 生产侧要提供的接口：`EvalMemoryBackend` 协议（川哥团队实现）

这是「让 langgraph 提供对应接口」的核心交付物。川哥团队把生产记忆子系统包成实现下面协议的一个类。**红线：剥离 agent 推理层——只做记忆 observe/retrieve，不产生「回给用户的答案」**（答题由 harness 统一 LLM 做，和三家一视同仁）。

```python
from typing import Protocol
from dataclasses import dataclass

# ---- 构造时注入(评测控制,对齐三家统一注入) ----
# backend = ProductionMemoryBackend(
#     llm_client=<OpenAI兼容client>,      # formation/consolidation/read-synthesis 都用它
#     embed_client=<统一 bge>,            # store 检索 embedding
#     store_namespace="kidsbench_eval",   # 独立隔离评测 store,不碰生产数据
# )

@dataclass
class ObserveResult:
    produced_memory_ids: list[str]   # 这轮形成产生/更新的 memory id
    consumed_turn_ids: list[str]     # 这次形成消费了哪些 turn(多对多,供 sidecar+Attribution)
    write_llm_tokens: int            # 本轮写入烧的 LLM token(成本计量;晚绑定=0)

@dataclass
class RetrievedMemory:
    memory_id: str
    text: str
    score: float                     # 归一化 [0,1]
    source_turn_ids: list[str]       # backend 能给则给(native/wrapped),给不了留空走 sidecar 兜底
    memory_type: str                 # semantic|episodic|procedural|unknown(受控词表)
    timestamp: float | None

@dataclass
class RetrieveResult:
    memories: list[RetrievedMemory]
    read_llm_tokens: int             # 本次检索烧的 LLM token(晚绑定 synthesis 在这里;早绑定≈0)
    consumed_turn_ids: list[str]     # 喂给 synthesis 的全部 turn(供 Ablation/Utilization 归因)

@dataclass
class BackendProfile:
    internal_llm: str                # 注入校验:必须 = harness 注入的 model
    internal_embed: str
    formation_trigger: str           # "on_write" | "lazy_background"
    retrieval_uses_llm: bool         # read 是否调 LLM(范式诚实暴露,不封杀)
    schema_type: str                 # "unstructured"|"structured"|"graph"|"typed_profile"
    memory_id_stability: str         # "stable"|"versioned"|"ephemeral"
    supports_source_mapping: bool    # 能否原生给 source_turn_ids(否则 adapter 走 sidecar)
    # paradigm 连续坐标(早/晚绑定定位,制图用)
    write_llm_tokens_per_turn: float
    read_llm_tokens_per_query: float

class EvalMemoryBackend(Protocol):
    def observe(self, user_id: str, turn_id: str, session_id: str,
                role: str, text: str, timestamp: float) -> ObserveResult: ...
    def drain(self, user_id: str) -> None: ...        # flush gate:强同步把 background formation 跑完才返回
    def retrieve(self, user_id: str, query: str, top_k: int,
                 current_timestamp: float | None) -> RetrieveResult: ...
    def consolidate(self, user_id: str) -> tuple[int, int, str]: ...  # (count, llm_tokens, phase)
    def purge(self, user_id: str) -> int: ...         # 物理删 namespace,同步,删后 retrieve 必空
    def describe(self) -> BackendProfile: ...
    # 评测控制(准入红线)
    def set_deterministic(self, seed: int) -> None: ...
    def set_formation_immediate(self, on: bool) -> None: ...  # 强制每 turn 立即结算,绕过触发阈值
```

### langmem→协议 的映射建议 `[需核实生产形态]`
| 协议方法 | langmem/langgraph 落点（推断，待核实） |
|---|---|
| observe | `create_memory_store_manager(llm, store=...)` 处理一条 message；记录 input turn → output memory ids |
| drain | 若用 `ReflectionExecutor`(debounced background)：强制 flush pending tasks + 轮询 store 直到形成完成 |
| retrieve | `store.search(namespace, query)`；若生产带 read-synthesis 则保留并报 read_llm_tokens |
| consolidate | 生产显式固化入口；无则 no-op，phase=write_time |
| purge | `store.delete` 整个 namespace（物理，InMemoryStore=清字典 / PostgresStore=TRUNCATE 评测 schema）|
| describe | 川哥团队如实填（这步会暴露 LLM 能否注入、是早/晚绑定）|

---

## 2. KidsBench 侧：`LangGraphAdapter` 怎么消费（cc 实现，对标 mem0_adapter）

继承 `MemoryAdapter`，和三家完全同构：注入 sidecar/embedding_service、track_metrics/wrap_errors 装饰器、source_turn_ids sidecar 兜底。

### 7+1 方法映射对照表（左=契约方法，对标 mem0_adapter.py 结构）

| 契约方法 | LangGraphAdapter 实现 | 和三家一致性 |
|---|---|---|
| `write(user_id, turn)` | `r = backend.observe(...)`；`sidecar.put(user_id, turn_id, r.produced_memory_ids)`；`WriteStats(cost_token=r.write_llm_tokens, raw={consumed_turn_ids})` | 同 mem0：sidecar 记 turn→memory；**新增 cost_token 如实填**（Pareto 用） |
| `batch_write` | 循环 observe（保 1:1 lineage，同 mem0 preserve_lineage） | 同 mem0 |
| `read(user_id, query, opts)` | `r = backend.retrieve(...)`；每个 mem 填 `source_turn_ids`（backend 给或 sidecar 反查）+ `source_embedding=embed(text)`（统一空间辅路）；`ReadResult(cost_token=r.read_llm_tokens, raw={consumed_turn_ids})` | 同三家 source 双路；**新增 read cost + consumed_turn_ids（归因用）** |
| `clear(user_id)` | `backend.purge(user_id)` + `sidecar.clear_user` | 同三家物理删 |
| `flush(user_id)` | `backend.drain(user_id)`（强同步 gate） | 同 graphiti 等索引就绪，**langgraph 关键：drain background** |
| `consolidate(user_id)` | `backend.consolidate(...)`；`ConsolidateStats(cost_token, consolidation_phase)` | 同 memoryos/graphiti 显式固化 |
| `get_dependencies` | postgres-store/internal_llm/internal_embed（从 describe） | 同三家 |
| `get_stats` | sidecar + metrics + paradigm_position（write/read tokens） | 同三家 + 新增 paradigm 坐标 |
| `get_capability_profile` | 从 `backend.describe()` 映射（见 §3） | 同三家 |
| `get_injected_providers` | `{internal_llm, internal_embed}` from describe | 同三家，verify_unified_injection 校验 |

### source_turn_ids 兜底（和三家完全同范式）
- backend `supports_source_mapping=True` → 直接用 `RetrievedMemory.source_turn_ids`，标 `provenance_mode=native/wrapped`
- 否则 → adapter 用 sidecar：observe 时存 `consumed_turn_ids → produced_memory_ids`，read 时反查，标 `provenance_mode=wrapped`
- 终极兜底 → `source_embedding` cosine 反查，标 `computed`

---

## 3. paradigm_tags + capability（体现范式新框架）

```python
# LangGraph 系统的 paradigm 标签(3 轴受控 + 连续坐标)
paradigm_tags = {
    "representation": "<structured_fact|graph|typed_profile>",  # 看 describe().schema_type
    "write_synchronicity": "<write_through|lazy_consolidate>",  # 看 formation_trigger
    "temporal_tracking": "<none|implicit_decay|explicit_timeline>",
}
# 连续坐标(进 get_stats,制图定位早/晚绑定)
paradigm_position = (describe().write_llm_tokens_per_turn, describe().read_llm_tokens_per_query)
```

capability 关键项（**诚实暴露，read 用 LLM 不再判负，只标 + 计成本**）：
- `retrieval_uses_llm`: 如实标。read 烧 token 全进 cost_token（Pareto 吸收，不封杀）
- `write_semantic_sync`: lazy_background → `wrapped(drain_required)`；on_write → native
- `cognitive_type_filter`: 有 memory_type → `wrapped(LLM_typed_output)`（grok 纠偏：非 store-level native）
- `lineage_after_consolidate`: consolidation 合并后 turn 溯源是否保持

---

## 4. 双重身份（承接前面张力 A，本期只做线①）

| 线 | 接入方式 | 回答什么 | 本期 |
|---|---|---|---|
| **① 纯记忆层**（走 EvalMemoryBackend 协议，剥离推理） | 标准 7 接口，同台竞技 | 自研记忆机制本身在制图上落哪、哪些格子弱 | ✅ 本 spec |
| **② 完整系统基线**（含 agent 推理，read=agent 答题） | 超规格参照基线（像 Oracle），不走纯记忆协议 | 别家纯机制能否逼近/超过带推理的生产水位线 | ⬜ 后续，标 `ProductionFull` 基线 |

本期聚焦线①，和三家对齐。线②作为「生产水位线」参照基线后续单列。

---

## 5. 落地步骤 + 待川哥提供的事实

**实现顺序**：
1. cc 写 `EvalMemoryBackend` 协议定义 + `LangGraphAdapter`（消费协议，含 sidecar/注入/capability）+ 一个 `MockBackend`（先让 adapter 过 47 契约测试，不依赖生产）
2. 川哥团队实现 `ProductionMemoryBackend`（包装生产 langgraph 记忆子系统，实现协议）
3. 起 `.venv-langgraph`（第四独立 venv，依赖隔离，绝不降级三家 SDK）
4. 注册进 ADAPTER_FACTORIES → 跑 47 契约测试 + step1 准入红线校验（drain gate/物理清场/确定性/隔离）
5. smoke 端到端 → 接入完成

**待核实事实（no-guessing）**：
- 生产记忆子系统能否剥离成「observe/retrieve」纯记忆操作（不带 agent 推理）？`[需核实]`
- formation 是 on_write 还是 lazy_background(ReflectionExecutor)？有触发阈值吗？`[需核实]`
- formation/retrieval 的 LLM 能否运行时注入统一 client（还是硬编码/env）？`[需核实]`
- store 后端（PostgresStore/InMemoryStore/自研）？能否独立隔离评测 store？`[需核实]`
- retrieve 是否带 LLM synthesis？（决定它在 paradigm 坐标落早绑定还是晚绑定区）`[需核实]`

---

## 附：和三家接入的一致性保证清单
- ✅ 同继承 MemoryAdapter，同 7+1 方法
- ✅ 同 sidecar turn_id 兜底范式
- ✅ 同 EmbeddingService 统一辅路
- ✅ 同 get_injected_providers + verify_unified_injection 注入校验
- ✅ 同 47 契约测试（不放水）
- ✅ 同 track_metrics/wrap_errors 中间件
- ➕ 新增（范式框架要求，所有家统一）：cost_token 如实填（Pareto）、paradigm_position 坐标、consumed_turn_ids（Ablation 归因）
