# Adapter 开发指南

> **目的**：教你从 0 写一个新 Adapter，让某个第三方记忆系统（Mem0/Letta/MemoryOS/Graphiti/Hermes 等）接入 KidsBench 评测框架。

## TL;DR

```
1. 在 src/kidsbench/adapters/ 新建 your_adapter.py
2. 继承 MemoryAdapter，实现 8 个抽象方法（含 batch_write 默认实现）
3. 复用 L0.5 中间层装饰器（metrics / errors / observe / sidecar）
4. 声明 paradigm_tags（4 轴 + cognitive）
5. 声明 get_capability_profile（含 5 Lane 适配性）
6. 在 ADAPTER_FACTORIES 注册
7. 跑 pytest tests/test_contract.py 必过
8. 写 adapter 专属测试 tests/adapters/test_your_adapter.py
```

---

## 1. 三层架构定位

```
L3 题库 (questions/*.jsonl)
   ↓
L2 Harness 主控 (run_eval.py)
   ↓ 用契约调用
L1 MemoryAdapter ABC ← 你必须满足的契约
   ↓ 继承
L0 你的 Adapter（仅做范式翻译）
   ↓ 复用
L0.5 中间层（metrics/errors/sidecar/embedding/llm_client/preflight/fallback/virtual_clock/rate_limiter）
```

**Adapter 的职责**：把第三方 SDK（mem0/letta/graphiti 等）的 API 翻译成 8 个契约方法。其他事（指标采集、异常包装、turn_id 兜底）都交给 L0.5 装饰器。

---

## 2. 8 个契约方法对照表

| 方法 | 强制实现？ | 你做的事 | L0.5 帮你做的事 |
|---|---|---|---|
| `write(user_id, turn)` | ✅ 必须 | 调原生 add/insert，**塞 turn_id 到 metadata 或 sidecar** | metrics 自动填 latency_ms / errors 包装异常 |
| `batch_write(user_id, turns)` | ⚠️ 强烈建议 | 调原生 batch API（没有就用默认实现） | 同上 |
| `read(user_id, query, opts)` | ✅ 必须 | 调原生 search，**Memory 对象必填 source_turn_ids 或 source_embedding** | metrics + observe |
| `clear(user_id)` | ✅ 必须 | 调原生 delete_all，**必须物理同步删** | metrics + sidecar.clear_user |
| `flush(user_id)` | ✅ 必须 | 等异步索引就绪（毫秒级） | metrics |
| `consolidate(user_id)` | ⚠️ 选择实现 | 触发 LLM 语义固化（秒级，吃 token）。无固化逻辑则默认 no-op | metrics + rate_limiter |
| `get_dependencies()` | ✅ 必须 | 声明依赖列表，**含 internal_llm / internal_embed** | preflight 跑 |
| `get_stats(user_id)` | ✅ 必须 | 返回内部计数 | — |
| `get_capability_profile()` | ✅ 必须 | 声明 11 个能力 + 5 档 Lane 适配性 | 评测 ablation 据此分组 |

---

## 3. 三大翻译点（必须做对）

### ① write: turn_id 编码

候选系统**绝大多数不会原生保留你的 turn_id**（Mem0 拆 entity、MemoryOS 抽象、Graphiti 建 KG）。三种兜底方案：

```python
# 方案 A: metadata 注入（Mem0/Letta 适用）
self.client.add(messages=..., user_id=user_id,
                metadata={"turn_id": turn.turn_id, "ts": turn.timestamp})

# 方案 B: sidecar 兜底（MemoryOS/Graphiti 适用，候选不支持 metadata）
result = self.client.write(...)
self.sidecar.put(user_id, turn.turn_id, [m.id for m in result.created_memories])

# 方案 C: 辅路 embedding（终极兜底）
# read 时返回 Memory 必填 source_embedding，Harness 用 cosine 反查 gold_turn
```

**禁止**：硬编码 `return ["t_001"]` 占位符。契约测试会抓。

### ② read: 装箱单格式

返回的 `Memory` 必须填**主路 OR 辅路** 之一（最好都填）：

```python
return ReadResult(memories=[
    Memory(
        memory_id="mem0_xxx",       # adapter 内部 id，任意
        text="...",
        score=0.87,                 # 归一化到 [0, 1]
        source_turn_ids=["t_001", "t_003"],   # 主路（多对多列表！）
        source_embedding=[...],     # 辅路（统一 embedding 服务）
        timestamp=1700000000.0,
        metadata={...},
    ),
    ...
])
```

**多对多关系**（gemini 关键提醒）：1 个 memory 可能合并自 N 个 turn，必须列全 source_turn_ids（不能只留一个）。

### ③ clear: 物理同步删

```python
def clear(self, user_id):
    # 必须真删（不是软删 deleted=true）
    # 必须同步等完成（不能异步返回 200 立即返回）
    self.client.delete_all(user_id=user_id)
    self.sidecar.clear_user(user_id)  # 同时清 sidecar
    # 契约测试会立即 read，必须返回空
```

---

## 4. 五种补齐策略（capability_profile 声明）

候选系统原生缺失某能力时，你必须显式声明用了什么策略：

| 策略 | level | 适用场景 |
|---|---|---|
| **native** | `native` | 候选原生支持（如 Mem0 metadata 原生保留） |
| **wrapped** | `wrapped` | Adapter 用真数据包装（metadata.turn_id 注入+取回） |
| **computed** | `computed` | Adapter 用真数据计算（cosine 反查 turn_id） |
| **simulated** | `simulated` | 经验估算（必须声明误差，如 `cost_token` ±3%） |
| **declared** | `declared` | 明确不支持，返回 None / 空列表（不是占位） |
| **unsupported** | `unsupported` | 候选物理上无法实现 |

**示例**：

```python
def get_capability_profile(self):
    caps_map = {
        "physical_clear": ("native", "mem0.delete_all 同步物理删"),
        "turn_id_traceback": ("wrapped", "metadata.turn_id 注入+取回 + sidecar 兜底"),
        "cognitive_type_filter": ("computed", "search 后用 metadata.cognitive_type 过滤"),
        "score_normalized": ("wrapped", "mem0 返 0-1 分数直接用"),
        "concurrent_safe": ("native", "user_id 物理隔离"),
        "cost_accounting": ("native", "litellm 返 usage"),
        "embedding_export": ("computed", "mem0 内部 embedding 异构，Harness 用统一服务重 embed"),
        "flush_blocking": ("native", "mem0 同步写"),
        "consolidate_explicit": ("native", "mem0 已在 write 时同步固化"),
        "batch_write_native": ("native", "mem0.add 接受 list messages"),
        "write_semantic_sync": ("native", "返回前事实已可查"),
    }
    caps = [Capability(feature=f, level=lvl, note=note)
            for f, (lvl, note) in caps_map.items()]
    return CapabilityProfile(
        adapter_name=self.name,
        capabilities=caps,
        lane_compatibility={
            "A1": "compatible",     # mem0 支持换 Qwen
            "A2": "compatible",     # 原生支持 GPT-4
            "A3": "degraded",       # 本地 7B 可换但 entity 提取格式可能崩
            "B": "compatible",      # 自由内层就是默认
            "C": "incompatible",    # mem0 强依赖 LLM 提取
        },
        lane_notes={
            "A3": "Qwen-2.5-7B-Instruct entity 提取格式偶有崩溃，需开 Verifier 兜底",
            "C": "mem0 必须有 internal_llm，无法跑纯检索 Lane C",
        },
    )
```

---

## 5. Lane 适配性判定流程

收到一个新候选系统，按这张表判定 Lane 兼容性：

```
问 1: 该 adapter 内部需要调 LLM 吗？
├── 否 → Lane C: compatible
└── 是 → 问 2

问 2: 内部 LLM 可以替换吗？（看 SDK 文档 / 源码）
├── 不能 → 锁定的 LLM 决定档位
│   ├── 锁 OpenAI GPT-4 → A2 compatible, A1/A3 incompatible
│   ├── 锁 Qwen → A1 compatible, A2/A3 incompatible
│   └── 锁本地 7B → A3 compatible, A1/A2 incompatible
└── 能 → 问 3

问 3: 换成 Qwen3-Max / GPT-4 / 本地 7B 后，prompt 模板还能稳定吗？
├── 稳定 → 对应档位 compatible
├── 偶有崩溃 → degraded（写 lane_notes 说明）
└── 完全崩溃 → incompatible
```

**Lane B（自由内层）总是 compatible**（除非 adapter 完全不依赖 LLM 那就走 Lane C）。

---

## 6. 范式标签（paradigm_tags）

每个 adapter 必须声明 4 轴 + 1 cognitive：

```python
paradigm_tags = {
    "representation": "vector+entity",   # raw_text / vector / entity / temporal_kg / hybrid
    "retrieval":      "hybrid_rerank",   # vector / bm25 / multi_hop / rerank / 16_recipes
    "write_policy":   "reactive",        # append_only / consolidation / event_chain / reactive
    "controller":     "rule",            # rule / llm_driven / os_inspired / oracle
    "cognitive":      ["semantic"],      # episodic / semantic / procedural（可多选）
}
```

**为什么**：评测分组 ablation 用，能让"vector 范式 vs KG 范式 vs 分层范式"的得分差异自动归类。

---

## 7. L0.5 中间层复用范例

### 装饰器叠加（推荐顺序：metrics 外层 → errors → sidecar）

```python
from kidsbench.contract import MemoryAdapter
from kidsbench.middleware import track_metrics, wrap_errors, sidecar_write

class Mem0Adapter(MemoryAdapter):
    name = "mem0"

    @track_metrics(method="write")     # 自动填 latency_ms / cost
    @wrap_errors(mapping={             # 异常映射
        "mem0.LLMError": LogicError,
        "httpx.TimeoutException": TimeoutError_,
    })
    @sidecar_write                     # 自动写 turn_id↔memory_id 映射
    def write(self, user_id, turn):
        return self.client.add(
            messages=[{"role": turn.role, "content": turn.text}],
            user_id=user_id,
            metadata={"turn_id": turn.turn_id, "ts": turn.timestamp},
        )
```

### 用 VirtualClock 而非 time.time()

```python
from kidsbench.middleware import get_clock

def some_temporal_logic(self):
    now = get_clock().now()   # ← 而不是 time.time()
    # 否则 K12 跨天评测时间戳全乱
```

### 用 EmbeddingService 而非每家自己跑 embedding

```python
def __init__(self, ..., embedding_service=None):
    self.embed = embedding_service or get_default_embedding()

def read(self, ...):
    memories = self.client.search(...)
    # 给每个 memory 补 source_embedding（统一空间）
    for m in memories:
        m.source_embedding = self.embed.embed([m.text])[0]
```

---

## 8. 注册 + 测试

### 在 `tests/test_contract.py` 注册

```python
def make_mem0() -> Mem0Adapter:
    # 测试只起内存版（in-process），不连云端
    return Mem0Adapter(config={"backend": "local"})

ADAPTER_FACTORIES["mem0"] = make_mem0
```

跑 `pytest tests/test_contract.py` 47 测试必须全过（不能为新 adapter 放水改测试）。

### Adapter 专属测试 `tests/adapters/test_mem0.py`

写真实业务测试（非契约层）：
- 中文实体抽取正确性
- 多 user 并发隔离
- consolidate 后 read 召回率
- preflight 在 mem0 未装时给出清晰错误

---

## 9. Wave 接入路线（当前规划）

| Wave | Adapter | 优先级 | 范式价值 |
|---|---|---|---|
| W0 | NoMemory / FullHistory / Oracle | ✅ 已完成 | 地板/对照/天花板 |
| **W1** | **Mem0 / MemoryOS / Graphiti** | 🟡 进行中 | 三种主流范式 |
| W2 | Letta / Hindsight / Cognee | ⬜ | LLM-driven / 反思 / pipeline |
| W3 | Hermes（自研） | ⬜ | 自验 |
| V1+ | SimpleMem / MemMachine / Memobase | ⬜ | profile / 压缩 / 保真 |

---

## 10. 常见错误清单

| 错误 | 后果 | 修法 |
|---|---|---|
| 硬编码 `source_turn_ids=["t_001"]` | 契约测试不过 + 评测召回率全是假的 | 用 sidecar 兜底 |
| clear 写成软删（`deleted=true`） | 幽灵记忆残留，跨题污染 | 调原生 `delete_all` 或物理 drop |
| flush 写成空函数（pass） | 异步索引未到位，read 返空 | 真等索引就绪 |
| consolidate 跟 flush 写一样 | 评测时序幻觉（write 完立即 read 拿不到固化结果）| 拆开，consolidate 必须真触发 LLM |
| 用 `time.time()` 而不是 `get_clock()` | K12 跨天评测时间错乱 | 用 VirtualClock |
| metadata 塞了 turn_id 但取回时丢 | 主路失效 | 跑 sidecar 兜底 + 写 capability `wrapped` |
| LLM 调用不走 rate_limiter | 限流打爆 + 评测中断 | 包一层 `rate_limiter.acquire("dashscope")` |
| paradigm_tags 漏一个轴 | 评测 ablation 分组失败 | 必须填全 4 轴 + cognitive |
| lane_compatibility 漏档 | 测试不过 | 5 档全声明（哪怕都 compatible） |
