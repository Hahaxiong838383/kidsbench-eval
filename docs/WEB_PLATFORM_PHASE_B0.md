# KidsBench Web Platform — B0 阶段实施计划

> 阶段：B0（架构白盒展示）
> 父 SPEC：[`WEB_PLATFORM_SPEC.md`](./WEB_PLATFORM_SPEC.md)
> 工时预算：1.5d
> 状态：待开工

---

## 1. B0 目标

**用 web 形式把现有 3 adapter × 3 记忆系统的"适配、架构、工作逻辑"白盒化**，让人打开就能看懂：

- 一个 adapter 是怎么把 Turn 喂进 SDK 的？（用了哪个类、调用了哪个方法）
- middleware 包了什么？（embedding service 怎么统一、graphiti async/sync bridge 怎么搞）
- 记忆系统底层存了什么？（mem0 的 Qdrant collection 字段、memoryos 三层结构、graphiti FalkorDB 图谱）
- 当前 3 个记忆系统跑成什么样了？（实时拉点数 / 节点边数 / 三层占用）
- 历史 run 的成绩是多少？（mem0/memoryos/graphiti 的 bge 切换前后对比）

### 不在 B0 范围（明确不做）

- ❌ 题库 CRUD（B2 做）
- ❌ Pipeline 时间线 swimlane（B1 做）
- ❌ SSE 实时事件流（B1 做）
- ❌ 触发新 run（B3 做）
- ❌ 横向 diff 召回结果（B2 做）
- ❌ 历史 run 详情页（B1 做，B0 只列 summary）

### 验收标准

- ✅ 川哥手机打开 `kidsbench.cli4.hahaxiong.cc`（HTTPS），看到 5 个核心页面
- ✅ 每个 adapter 详情页含 file:line 可点击代码索引（GitHub 链接或 VS Code 链接）
- ✅ 实时状态面板能正确显示 Qdrant 点数 / FalkorDB 节点数 / MemoryOS 三层
- ✅ 历史 run 概览页能从 `runs/*_bge/` 自动读 final.json 渲染
- ✅ QNAP container 重启后自动恢复

---

## 2. 输出清单

### 2.1 5 个核心页面

#### 页面 ① 首页 / 架构总览

URL：`/`

内容：
- 一张大图：完整数据流（题目 → harness → 3 adapter → 3 memory system → 评分），SVG 或纯 CSS Grid 绘制
- 关键统计 banner：当前题库题数 / 已跑 run 数 / 最近成绩
- 3 个 adapter 卡片（点击进详情页）
- 3 个记忆系统卡片（点击进详情页）

数据来源：
- 静态架构图：手画 SVG 嵌入
- 题库数：读 `questions/smoke.jsonl`
- run 数：扫 `runs/` 子目录数
- 最近成绩：读 `runs/*_bge/*/final.json`

#### 页面 ② Adapter 详情页（3 个，参数化路由）

URL：`/adapter/mem0` / `/adapter/memoryos` / `/adapter/graphiti`

每个 adapter 页内容：
- **基本信息**：SDK 包名 / 版本 / GitHub 链接 / 安装方式
- **入口类**：`Mem0Adapter` 等，file:line 链接
- **关键方法实现**：`write` / `recall` / `clear` 三个方法的代码片段（语法高亮）+ 解释
- **middleware 依赖**：用了 embedding.py / graphiti_compat.py 的哪个函数
- **数据流图**：write 路径 + recall 路径（带 LLM call / embedding call 标注）
- **配置参数**：当前 harness/run_eval.py 用的配置（模型 / 维度 / 路径）
- **已知问题**：从 `docs/EMBEDDING_KNOWN_ISSUES.md` 抓相关条目

数据来源：
- 静态信息 + 代码引用：B0 阶段硬编码到一个 JSON 配置文件
- 配置参数：扫 `harness/run_eval.py` 解析

#### 页面 ③ 记忆系统详情页（3 个）

URL：`/memory/mem0` / `/memory/memoryos` / `/memory/graphiti`

每个记忆系统页内容：
- **存储后端**：Qdrant / 内置 faiss / FalkorDB
- **数据结构**：collection schema / 三层 (short/mid/long) 结构 / 图谱 schema
- **工作机制**：怎么写入 / 怎么检索 / 衰减策略
- **实时状态快照**（核心）：当前点数 / 节点边数 / 三层占用 / 最新写入时间
- **数据可视化**：
  - mem0：Qdrant collection 列表 + 每个点数饼图
  - memoryos：三层占用堆叠条
  - graphiti：图谱节点 / 边数 + 最近 5 个 episode

数据来源：
- 实时状态：后端 API `/api/state/{mem0|memoryos|graphiti}`
- 静态结构图：手画 SVG

#### 页面 ④ 历史 Run 概览

URL：`/runs`

内容：
- 表格：所有 run 一行一条
  - 列：run_id / qid / adapter / 评分 / 时间 / 耗时
  - 默认按时间倒序
- 顶部：按 adapter / 按 qid 筛选
- 顶部：评分分布柱状图（mem0/memoryos/graphiti 三色）
- 点击行：B0 阶段只跳 `final.json` 原文（B1 做详情页）

数据来源：
- 后端 API `/api/runs`：扫 `runs/` 子目录读 `final.json`

#### 页面 ⑤ 系统状态 / 关于

URL：`/system`

内容：
- 部署信息：版本 / 后端 commit / 前端 commit
- Adapter SDK 版本一览
- LLM 模型当前用的：gemini-3.5-flash via GEMINI_PROXY
- Embedding 模型：BAAI/bge-small-zh-v1.5 (192MB, 512d)
- 已知 issues 摘要（来自 `EMBEDDING_KNOWN_ISSUES.md`）
- 链接：父 SPEC / 项目 GitHub / 飞书页

---

### 2.2 后端 API（B0 只读，6 个 endpoint）

| Method | Path | 功能 | 数据来源 |
|---|---|---|---|
| GET | `/healthz` | 健康检查 | 内置 |
| GET | `/api/architecture` | 架构索引（adapter / memory 静态信息）| 配置文件 |
| GET | `/api/state/mem0` | Qdrant 状态快照 | qdrant-client SDK |
| GET | `/api/state/memoryos` | MemoryOS 三层状态 | 读 memoryos 持久化目录 |
| GET | `/api/state/graphiti` | FalkorDB 图谱状态 | falkordb-client SDK |
| GET | `/api/runs` | 历史 run 列表 | 扫 `/app/runs/*_bge/*/final.json` |

### 2.3 前端技术栈

**正式版**（不走 streamlit / jinja 临时方案）：
- React 18 + Vite + TypeScript
- Tailwind CSS + shadcn/ui
- TanStack Query（数据获取）
- React Router（5 个页面）
- 静态架构图：纯 SVG（手画）或 mermaid（markdown 嵌）

**为什么 B0 就上正式技术栈**：B1+ 直接复用，不返工。

### 2.4 部署增量

- QNAP 起 2 个 container：`kidsbench-backend` + `kidsbench-frontend`
- HK nginx 加 server block `kidsbench.cli4.hahaxiong.cc`
- Mini frpc 加 2 个 proxy
- Let's Encrypt 申请子证书
- Basic Auth `.htpasswd-kidsbench`

---

## 3. 工时拆分

| 任务 | 工时 | 备注 |
|---|---|---|
| 后端骨架（FastAPI + 6 个 endpoint）| 0.3d | cc 主线 |
| 状态查询实现（mem0 / memoryos / graphiti SDK 调用）| 0.3d | cc 主线 |
| 前端骨架（Vite + Router + Layout）| 0.2d | cc 主线（或 codex） |
| 5 个页面（含 SVG 架构图）| 0.4d | cc 主线（或 codex） |
| Dockerfile + docker-compose | 0.1d | cc 主线 |
| QNAP 部署 + nginx + FRP + 证书 | 0.2d | cc 主线 |
| 端到端调试 | — | 含在各项 |
| **总计** | **1.5d** | |

---

## 4. 开工 checklist（按顺序）

### Day 1 上午（0.5d）：后端骨架

1. [ ] 在 `kidsbench-eval/` 下新建 `web/` 目录（前后端共生）
2. [ ] `web/backend/` 写 FastAPI 骨架 + 6 个 endpoint stub
3. [ ] 实现 `/api/state/mem0`（qdrant-client 连本地 / QNAP）
4. [ ] 实现 `/api/state/graphiti`（falkordb-client 连 QNAP 16379）
5. [ ] 实现 `/api/state/memoryos`（读 `/tmp/memoryos_persist/` 目录）
6. [ ] 实现 `/api/runs`（扫 `runs/*_bge/*/final.json`）
7. [ ] 本地 uvicorn 跑通 + curl 测每个 endpoint
8. [ ] 写 pytest 单测（mock SDK 调用）

### Day 1 下午（0.5d）：前端骨架 + 静态页

1. [ ] `web/frontend/` 用 `npm create vite@latest` 初始化（React + TS + Tailwind）
2. [ ] 装 shadcn/ui + TanStack Query + React Router
3. [ ] 写 Layout（顶部导航 + 主内容区）
4. [ ] 写 5 个页面骨架（路由 + 占位内容）
5. [ ] 实现首页（架构 SVG + 卡片）
6. [ ] 实现 Adapter 详情页（mem0/memoryos/graphiti 三选一）
7. [ ] 实现 Memory 详情页（接 `/api/state/*`）

### Day 2 上午（0.4d）：剩余页面 + 部署

1. [ ] 实现历史 Run 页（接 `/api/runs`）
2. [ ] 实现 System 页
3. [ ] 写 Dockerfile（backend + frontend）
4. [ ] 写 docker-compose.yml（含 healthcheck + depends_on）
5. [ ] 本地 docker-compose up 端到端测试

### Day 2 下午（0.1d）：QNAP 部署 + HK 链路

1. [ ] SSH 到 QNAP 拉 docker-compose
2. [ ] 加 Mini frpc proxy（kidsbench-web / kidsbench-api）
3. [ ] HK 申请 Let's Encrypt 子证书
4. [ ] HK nginx 加 server block
5. [ ] Basic Auth `.htpasswd-kidsbench` 生成
6. [ ] DNS CNAME `kidsbench.cli4.hahaxiong.cc`
7. [ ] 公网端到端验证（手机浏览器打开）

### 验收

1. [ ] 5 个页面手机能正常打开
2. [ ] 实时状态接口正确响应
3. [ ] 历史 run 数据正确渲染
4. [ ] 代码引用链接可点击（B0 阶段先指向本地路径，B1 上传到 GitHub 后改链接）
5. [ ] 容器重启自动恢复

---

## 5. 关键设计细节

### 5.1 静态架构图（SVG 手画）

**首页架构总览**示例（mermaid 渲染或手写 SVG）：

```
┌─────────────────────────────────────────────────────────────┐
│  题库（questions/smoke.jsonl）                              │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│  harness/run_eval.py                                        │
│  - make_mem0_adapter()                                      │
│  - make_memoryos_adapter()                                  │
│  - make_graphiti_adapter()                                  │
└──────┬───────────────┬────────────────┬────────────────────┘
       ↓               ↓                ↓
   ┌────────┐     ┌───────────┐    ┌──────────┐
   │ Mem0   │     │ Memoryos  │    │ Graphiti │
   │Adapter │     │ Adapter   │    │ Adapter  │
   └────┬───┘     └─────┬─────┘    └─────┬────┘
        │  EmbeddingService 统一中间件   │
        ↓               ↓                ↓
   ┌────────┐     ┌───────────┐    ┌──────────┐
   │ Qdrant │     │ Built-in  │    │ FalkorDB │
   │vec_512 │     │ faiss+LLM │    │ Graph KG │
   └────────┘     └───────────┘    └──────────┘
```

### 5.2 Adapter 详情页内容范式（mem0 为例）

```
# Mem0 Adapter

## 基本信息
- SDK: mem0ai==2.0.4
- GitHub: https://github.com/mem0ai/mem0
- 安装: pip install mem0ai

## 入口类
src/kidsbench/adapters/mem0_adapter.py:23 → Mem0Adapter(MemoryAdapter)

## write() 实现
file: src/kidsbench/adapters/mem0_adapter.py:67-89
逻辑: 
1. Turn 转 mem0 messages 格式
2. 调 self.client.add(messages=[...], user_id=user_id)
3. mem0 内部用 LLM (gemini-3.5-flash) 抽取 facts
4. 抽取的 facts 经 embedding (bge-small-zh-v1.5) 入 Qdrant collection

## recall() 实现
file: src/kidsbench/adapters/mem0_adapter.py:91-115
逻辑:
1. query 经 embedding 转 512 维向量
2. Qdrant 按 cosine 相似度召回 top-k
3. 返回 RecallResult(text=fact, score=similarity, metadata=...)

## middleware 依赖
- src/kidsbench/middleware/embedding.py:make_st_embedder (BAAI/bge-small-zh-v1.5)
- 不依赖 graphiti_compat.py

## 当前配置（harness/run_eval.py:make_mem0_adapter）
```python
config = {
    "vector_store": {"provider": "qdrant", "config": {
        "host": "127.0.0.1", "port": 6333,
        "collection_name": "kidsbench_eval_bge",
        "embedding_model_dims": 512,
        "path": "/tmp/kidsbench_qdrant_eval_bge",
    }},
    "embedder": {"provider": "huggingface", "config": {
        "model": "BAAI/bge-small-zh-v1.5",
        "embedding_dims": 512,
    }},
    "llm": {"provider": "openai", "config": {
        "model": "gemini-3.5-flash",
        "openai_base_url": "http://23.226.135.149:4000/v1",
    }},
}
```

## 已知问题
- bge-small-zh query/passage 前缀未加（gemini A.1）
- 详见 docs/EMBEDDING_KNOWN_ISSUES.md
```

### 5.3 实时状态拉取（关键技术决策）

**问题**：B0 后端跑在 QNAP container，怎么拉 Air 上的 mem0 / memoryos 状态？

**答案**：B0 阶段 **只展示 graphiti 实时状态**（因为 graphiti 的 FalkorDB 跑在 QNAP，QNAP container 本地访问 16379 就行）。

mem0 / memoryos 状态**展示 "最近一次跑 run 时的快照"**（从 `final.json` 的 stats 字段拉），不强求 Air 数据实时同步。

B1+ 引入 HTTP POST 通道后，Air 可以主动推状态到 QNAP，那时再补完 mem0/memoryos 的实时状态。

**B0 状态展示矩阵**：

| Adapter | 实时拉？ | 数据来源 |
|---|---|---|
| mem0 | ❌ | 最近一次 run 的 final.json stats |
| memoryos | ❌ | 最近一次 run 的 final.json stats |
| graphiti | ✅ | QNAP 本地 FalkorDB 16379 |

这是合理的 B0 取舍——B0 重点是**架构白盒**，不是"实时大屏"。

---

## 6. 下一步（B0 完成后）

B0 跑通后：
1. cc 写 B1 阶段实施计划（pipeline 时间线 + SSE + HTTP POST 事件流）
2. 川哥拍方向
3. cc 实施（或 codex 5/31 恢复后协同）

---

## 7. 关联文档

- 父 SPEC：[`WEB_PLATFORM_SPEC.md`](./WEB_PLATFORM_SPEC.md)
- Adapter 实现指南：[`ADAPTER_GUIDE.md`](./ADAPTER_GUIDE.md)
- 已知问题：[`EMBEDDING_KNOWN_ISSUES.md`](./EMBEDDING_KNOWN_ISSUES.md)
- Gemini 评审存档：`/tmp/gemini_kidsbench_web_review.md`

---

**审阅人**：川哥
**实施人**：cc 主线（codex 限额，5/31 恢复后协同）
**预计开工**：川哥拍方向后
**预计完工**：开工后 1.5d
