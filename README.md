# KidsBench Memory System Evaluation Framework

K12 儿童 AI 陪伴产品记忆系统评测框架。

## 目标

不是产品选型，是**反推自研记忆系统的设计原则**——通过统一契约层对多家记忆系统（Mem0/Letta/MemoryOS/Hermes 自研等）做对抗性评测，沉淀「什么范式在 K12 场景真正有效」。

## 五大评测维度

| ID | 维度 | 子维度 |
|----|------|--------|
| ① | 意图理解 | — |
| ② | 情绪识别 | — |
| ③ | 安全过滤 | — |
| ④ | 记忆召回 | ④a Episodic / ④b Semantic / ④c Procedural |
| ⑤ | 跨会话一致性 | ⑤a Episodic / ⑤b Semantic / ⑤c Procedural |

## 架构分层

```
L3 题库层 (questions/*.jsonl)
L2 Harness 主控 (run_eval.py)
L1 MemoryAdapter 契约层  ← 本仓核心
L0 各家适配器实现
```

## Adapter 契约（7 方法）

| 方法 | 用途 |
|------|------|
| `write(user_id, turn)` | 灌入对话历史 |
| `read(user_id, query, opts)` | 召回相关记忆 |
| `clear(user_id)` | 物理清场（强制真删） |
| `flush(user_id)` | 强制落盘 / 索引就绪 |
| `get_dependencies()` | preflight 自检 |
| `get_stats(user_id)` | 内部计数 / 耗时 |
| `get_capability_profile()` | 声明能 / 不能做什么（补齐策略锚点） |

## 三类基线

- **NoMemory**：不接任何记忆系统，地板基线
- **FullHistoryPrompt**：把所有历史直接塞 Prompt，对照基线（最难击败）
- **OracleMemory**：根据 `gold_memory_ids` 完美召回，天花板基线

## 开发路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| Day 1 | 仓库 + 契约层 + 3 基线 | 🟡 进行中 |
| Day 2 | Mem0Adapter | ⬜ |
| Day 3 | Letta + MemoryOS Adapter | ⬜ |
| Day 4 | Hermes Adapter（自研） | ⬜ |
| Day 5 | 5 adapter 契约一致性 + capability matrix | ⬜ |

## 设计文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 整体架构
- [`docs/ADAPTER_GUIDE.md`](docs/ADAPTER_GUIDE.md) 新增 adapter 步骤
- [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md) 各 adapter 能力对照（自动生成）

## 相关文档

完整 v3 评测协议在飞书：
- KidsBench v3 · 最简评测链路验证（架构 + 操作）
