# Embedding 模型 known issues（bge-small-zh-v1.5 切换）

> 来源：gemini 对抗审 2026-05-29，实测结果 + 风险记录
> 完整评审：`/tmp/gemini_bge_switch_review.md`

## 当前配置

三家 adapter 统一用 `BAAI/bge-small-zh-v1.5`：

| 项 | 值 |
|---|---|
| 模型 | BAAI/bge-small-zh-v1.5 |
| 大小 | 192MB |
| 维度 | 512 |
| 最大输入 | 512 tokens |
| 距离度量 | **必须 Cosine + L2 Normalize** |

## 实测分数变化

| Adapter | 之前 (all-MiniLM) | 现在 (bge-small-zh) | 变化 |
|---|---|---|---|
| mem0 | 6/6 | **6/6** | 持平 |
| memoryos | 5/6 | **6/6** ⬆ | +1 (q_003 paraphrase 失败修了) |
| graphiti | 5/6 | **5/6** | 持平（q_003 evasive 仍失败但召回信号改善） |

---

## ⚠️ 已知风险（题库定型后必须修）

### 1. Query/Passage 前缀缺失（gemini A.1）

**问题**：bge-small-zh 是**非对称检索模型**。BAAI 官方要求：
- Query 侧：必须加 instruction `"为该检索任务找到最相关的文档："`
- Passage 侧：绝不能加

**当前现状**：三家 adapter 都直接 `model.encode(text)`，**没区分 query/passage**。

**为什么当前题库还能 6/6**：
- mem0/memoryos 内部用 LLM 抽取事实（passage 是 LLM 重写的，query 也很简短）
- embedding 是辅路，LLM 才是主路
- 当前 6 题 smoke 偏简单

**何时会暴露**：
- 题库扩充后（更多无关 turn 混入 + query 措辞更间接）
- 召回 top-k 增大后（弱信号被噪音淹没）

**修复方案**（题库定型后实施）：
1. 在 `src/kidsbench/middleware/embedding.py` 新建 `BgeChineseEmbedder(EmbeddingService)` 类，加 `is_query: bool` 参数
2. encode 时根据参数决定是否加 instruction 前缀
3. 三家 adapter 改用此 service（替代各自 SDK 内置 embedder）

### 2. 距离度量必须 Cosine（gemini A.4）

**问题**：bge-v1.5 训练时假定 cosine + L2 norm。如果 backend 用 L2 (Euclidean) 距离，召回会完全错乱。

**当前现状**：
- **mem0 Qdrant**：默认 Cosine ✓（mem0 SDK 内置 normalize_embeddings=True）
- **Graphiti FalkorDB**：用图遍历 + 向量混合检索，已通过 graphiti 内部归一化 ✓
- **MemoryOS**：内部 faiss 索引可能 L2，**待确认**

**验收建议**：跑一次"团子布偶猫" vs "篮球比赛" 召回测试，看排序是否正确。

### 3. 最大 token 512 截断

**问题**：bge-small-zh 输入上限 512 tokens（约 1000-1500 中文字）。K12 题库长 turn 可能超长。

**当前现状**：当前 6 题 smoke 单 turn 最长 ~30 字，远低于上限，不影响。

**何时会暴露**：题库引入长文 turn（如读书报告 / 完整对话片段）

**修复方案**：题库引入长文本时实现 chunk 策略（按句号切，重叠 50 字）

### 4. K12 童言童语 OOV

**问题**：bge-small-zh 训练数据是新闻 / 百科 / 问答，**没有童言童语**（如"小屁孩"、"作业又来啦"、口语化片段）。

**当前现状**：未实测，但实测 4 case（团子/妈妈/数学/95 分）区分度都很好，初步看 OK。

**何时会暴露**：题库引入大量儿童口语化对话

**修复方案**：必要时换 bge-m3（多语言更宽容）或微调专属模型

### 5. 历史 Run 不可比（gemini C.1）

**问题**：runs/with_mem0/、runs/memoryos_only/、runs/with_graphiti_final/ 都是 all-MiniLM 数据，跟 runs/*_bge/ 数据**不可同表对比**。

**当前现状**：
- 旧 runs 文件已保留（不删）
- 新 runs 加 `_bge` 后缀区分
- README / 完工报告会标记切换时间点

**修复方案**：以 bge 切换为分界线，all-MiniLM 时代的数据归档备查，bge 时代的数据作为新基准。

---

## 6. 运行环境 / 部署相关

| 维度 | 当前 | 待办 |
|---|---|---|
| **HF cache 共享** | ~/.cache/huggingface/hub/ 三 venv 共用 | 部署时容器化 cache 持久化 |
| **模型下载** | 192MB 一次性下载 | 离线部署 build 时打包 |
| **推理后端** | CPU（Apple M5 ~50ms/句）| 未来上线看 QPS 决定 GPU |
| **替代路径** | / | DashScope text-embedding-v3 / 自建 docker / Studio Ollama qwen3-embedding |
