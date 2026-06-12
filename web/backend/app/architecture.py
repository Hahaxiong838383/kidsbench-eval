"""B0 静态架构数据（修订版：补齐 7 个 abstract + 2 个可覆写方法）。

数据来源：src/kidsbench/contract/adapter.py 的 MemoryAdapter ABC

九个方法分两组：
- 7 个 @abstractmethod（子类必须实现）：
    write / read / clear / flush / get_dependencies / get_stats / get_capability_profile
- 2 个有默认实现（可覆写）：
    batch_write（默认循环 write）/ consolidate（默认 no-op）

所有 file:line 在 2026-05-29 实测对齐。
按 source-analysis.md：必须含 file:line。
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/architecture", tags=["architecture"])


# ============================================================
# Contract 抽象基类元信息
# ============================================================

CONTRACT: dict = {
    "abc_class": {
        "name": "MemoryAdapter",
        "file": "src/kidsbench/contract/adapter.py",
        "line": 35,
        "doc": "记忆系统适配器抽象基类。子类必须实现 7 个 @abstractmethod。",
    },
    "abstract_methods": [
        "write", "read", "clear", "flush",
        "get_dependencies", "get_stats", "get_capability_profile",
    ],
    "overridable_methods": [
        "batch_write",   # 默认实现：循环 write
        "consolidate",   # 默认实现：no-op
    ],
    "helper_methods": [
        "health_check",  # 默认实现：检查 get_dependencies
    ],
    "design_principles": [
        "不可变：Adapter 不持有可变状态，全部在外部存储",
        "fail-fast：任何方法失败必须抛 AdapterError",
        "真实性：返回数据必须真实，禁止硬编码占位符",
        "能力诚实：通过 get_capability_profile() 显式声明补齐策略",
    ],
}


def _method(name: str, kind: str, file: str, line: int, logic: str) -> dict:
    """方法元信息构造器。kind ∈ {abstract, overridable}"""
    return {
        "name": name,
        "kind": kind,
        "file": file,
        "line": line,
        "logic": logic,
    }


# ============================================================
# Mem0 Adapter 方法清单（9 个）
# ============================================================

_MEM0_FILE = "src/kidsbench/adapters/mem0_adapter.py"

_MEM0_METHODS = [
    _method("write", "abstract", _MEM0_FILE, 137,
        "Turn → mem0 messages 格式 → self.client.add(messages, user_id) → "
        "mem0 内部 LLM (gemini-3.5-flash) 抽取 facts → "
        "facts 经 embedding (bge-small-zh-v1.5, 512d) 入 Qdrant collection"),
    _method("read", "abstract", _MEM0_FILE, 236,
        "query 经 embedding 转 512d 向量 → Qdrant cosine 召回 top-k → "
        "_extract_source_turn_ids 回填 source_turn_ids（主路） → 返回 ReadResult"),
    _method("clear", "abstract", _MEM0_FILE, 274,
        "self.client.delete_all(user_id=user_id) — 物理删 Qdrant 中该 user_id 所有 facts，同步等完成"),
    _method("flush", "abstract", _MEM0_FILE, 304,
        "mem0 无显式 flush API，本实现 = 检查 Qdrant 可达 + 返回 success。"
        "由于 write 内部已同步完成 embedding+upsert，flush 实质 no-op"),
    _method("get_dependencies", "abstract", _MEM0_FILE, 322,
        "返回 [mem0ai 包, Qdrant 端点, LLM endpoint, embedding model] preflight 清单"),
    _method("get_stats", "abstract", _MEM0_FILE, 352,
        "返回本地累计 {total_writes, total_reads, avg_write_ms, avg_read_ms}"),
    _method("get_capability_profile", "abstract", _MEM0_FILE, 360,
        "声明：representation=vector+entity, retrieval=topk, write_policy=reactive, "
        "controller=rule（mem0 内部 LLM 抽实体），lane_compat=vector_only"),
    _method("batch_write", "overridable", _MEM0_FILE, 163,
        "覆写：复用 mem0 SDK 的批量 client.add(messages=[...])，把多 turn 合并一次 LLM 调用，"
        "评测初始化灌历史 turn 时节省 50%+ LLM tokens"),
    _method("consolidate", "overridable", _MEM0_FILE, 317,
        "覆写为 no-op：mem0 在 write 时已同步抽完 facts，无需二次整理"),
]


# ============================================================
# MemoryOS Adapter 方法清单（9 个）
# ============================================================

_MOS_FILE = "src/kidsbench/adapters/memoryos_adapter.py"

_MEMORYOS_METHODS = [
    _method("write", "abstract", _MOS_FILE, 210,
        "Turn → MemoryosWrapper.add(user_input, system_response, metadata) → "
        "内部按 short→mid→long 三层迁移 + LLM 抽取 + faiss 索引建立"),
    _method("read", "abstract", _MOS_FILE, 256,
        "MemoryosWrapper.retrieve(query, context_window) → 跨三层混合召回 → "
        "_normalize_rows + _collect_source_turn_ids 回填 → ReadResult"),
    _method("clear", "abstract", _MOS_FILE, 312,
        "MemoryosWrapper.reset_all() → 删 persist 目录 + 重建 Memoryos 实例（无原生 reset API 的兜底）"),
    _method("flush", "abstract", _MOS_FILE, 322,
        "强制把 sidecar faiss 索引落盘 + 等三层迁移任务排空，毫秒级"),
    _method("consolidate", "overridable", _MOS_FILE, 338,
        "覆写：self.real_mm.consolidate() 调 LLM 做 short→mid→long 语义整理，秒级吃 token。"
        "评测协议要求 batch_write → flush → consolidate → read 四步分别可触发"),
    _method("get_dependencies", "abstract", _MOS_FILE, 373,
        "返回 [memoryos 包, 持久化目录可写, embedding model, LLM endpoint, rate limiter] preflight"),
    _method("get_stats", "abstract", _MOS_FILE, 386,
        "三层占用 {short_size, mid_size, long_size} + 累计 read/write 计数 + 平均延迟"),
    _method("get_capability_profile", "abstract", _MOS_FILE, 402,
        "声明：representation=三层语义压缩, retrieval=多层召回+rerank, "
        "write_policy=reactive+consolidate, controller=LLM, cognitive=[episodic, semantic]"),
    _method("batch_write", "overridable", _MOS_FILE, 242,
        "MemoryOS 无原生批量 API，覆写仅做循环 write + 共享 rate limiter，无 token 节省"),
]


# ============================================================
# Graphiti Adapter 方法清单（9 个）
# ============================================================

_GRA_FILE = "src/kidsbench/adapters/graphiti_adapter.py"

_GRAPHITI_METHODS = [
    _method("write", "abstract", _GRA_FILE, 88,
        "Turn → episode body → graphiti.add_episode(name, episode_body, group_id=user_id) → "
        "LLM 抽实体 + 关系 → 入 FalkorDB 图谱（节点 + 边）。经 _RealGraphitiWrapper 同步桥"),
    _method("read", "abstract", _GRA_FILE, 183,
        "graphiti.search(query, group_ids=[user_id], num_results=k) → "
        "混合检索（embedding 召回 + 图遍历）→ _resolve_turn_ids_via_graph 回填 → ReadResult"),
    _method("clear", "abstract", _GRA_FILE, 241,
        "graphiti.delete_episodes_by_group_id(user_id) → 真删图谱该 user_id 的所有 Episode/Entity/Edge"),
    _method("flush", "abstract", _GRA_FILE, 270,
        "等 wrapper.flush_pending → graphiti 后台异步 KG 整合任务全部完成，毫秒级（非 consolidate）"),
    _method("consolidate", "overridable", _GRA_FILE, 282,
        "覆写：graphiti.build_communities() 调 LLM 抽实体社区聚类 + 概要节点，秒级吃 token"),
    _method("get_dependencies", "abstract", _GRA_FILE, 302,
        "返回 [graphiti-core 包, FalkorDB 可达, LLM endpoint, embedding model] preflight"),
    _method("get_stats", "abstract", _GRA_FILE, 325,
        "_collect_graph_counts → FalkorDB 该 user_id 子图的 {episodes, entities, edges} + 累计 write/read"),
    _method("get_capability_profile", "abstract", _GRA_FILE, 337,
        "声明：representation=KG, retrieval=hybrid(vector+graph), "
        "write_policy=reactive+consolidate, controller=LLM, cognitive=[episodic, semantic, relational]"),
    _method("batch_write", "overridable", _GRA_FILE, 129,
        "覆写：检测到 graphiti.add_episode_bulk 时走批量 API，减少 LLM 抽取调用 30-50%"),
]


# ============================================================
# Hindsight Adapter 方法清单（10 个，recall/reflect 双模式）
# ============================================================

_HS_FILE = "src/kidsbench/adapters/hindsight_adapter.py"

_HINDSIGHT_METHODS = [
    _method("write", "abstract", _HS_FILE, 165,
        "Turn → client.retain(bank_id, content, timestamp, metadata={turn_id,...}) → "
        "Hindsight 内部 LLM 同步抽取 facts/entities/relationships 入 pg0。"
        "sidecar 写前查重（retain 非幂等，防 retry 重复记忆）；usage.total_tokens 如实计入 cost_token"),
    _method("read", "abstract", _HS_FILE, 201,
        "范式旋钮分叉：mode=recall → client.recall（向量+BM25+图+时序四路 → RRF → bge-reranker 重排，"
        "不调 LLM，read 成本 0）；mode=reflect → client.reflect（LLM agent 多轮合成 mental model，"
        "synthesis 作首条 Memory 标 synthesized，cost 计 usage）。溯源：recall 走 metadata.turn_id（wrapped），"
        "reflect 走 embedding 辅路（computed）"),
    _method("clear", "abstract", _HS_FILE, 307,
        "client.delete_bank(bank_id) → 六步级联物理删（documents→memory_units→invalidated→entities→banks→"
        "DROP per-bank HNSW 索引），实测交叉删除不互伤；同时清 sidecar"),
    _method("flush", "abstract", _HS_FILE, 317,
        "retain 默认同步（返回前抽取完成，实测写完立即可召回 20/20）→ flush 轻量 no-op"),
    _method("consolidate", "overridable", _HS_FILE, 324,
        "mode=recall → 禁 LLM（no-op，防「廉价检索点」成本归属混淆）；"
        "mode=reflect → POST /v1/default/banks/{bank}/consolidate 显式触发（auto-consolidation 已关）"),
    _method("get_dependencies", "abstract", _HS_FILE, 358,
        "返回 [hindsight-server(embedded pg0), 注入 LLM(llm_base_url), 统一 embedding, bge-reranker-v2-m3] preflight"),
    _method("get_stats", "abstract", _HS_FILE, 397,
        "返回 {mode, sidecar 计数, metrics 快照}"),
    _method("get_capability_profile", "abstract", _HS_FILE, 404,
        "按 mode 分别申报：recall=turn_id wrapped / Lane C compatible；"
        "reflect=turn_id computed / retrieval 用 LLM / Lane C incompatible"),
    _method("batch_write", "overridable", _HS_FILE, 77,
        "默认循环 write（保 1:1 metadata 溯源；retain_batch 会牺牲 per-turn metadata 粒度）"),
    _method("close", "overridable", _HS_FILE, 453,
        "显式释放 client 连接（防 aiohttp unclosed session）"),
]


# ============================================================
# 4 个 Adapter 完整元信息
# ============================================================

ADAPTERS: dict = {
    "mem0": {
        "name": "Mem0",
        "sdk": {
            "package": "mem0ai",
            "version": "2.0.4",
            "github": "https://github.com/mem0ai/mem0",
            "install": "pip install mem0ai==2.0.4",
        },
        "entry_class": {
            "name": "Mem0Adapter",
            "file": _MEM0_FILE,
            "line": 71,
        },
        "methods": _MEM0_METHODS,
        "middleware_deps": [
            "EmbeddingService（src/kidsbench/middleware/embedding.py）— 统一 bge-small-zh-v1.5",
        ],
        "storage": "Qdrant（vector_db, cosine, 512d）",
        "venv": ".venv-mem0",
        "known_issues": [
            "bge-small-zh 是非对称模型，Query 缺 instruction 前缀（gemini A.1，详见 EMBEDDING_KNOWN_ISSUES.md）",
        ],
    },
    "memoryos": {
        "name": "MemoryOS",
        "sdk": {
            "package": "memoryos",
            "version": "main (GitHub install)",
            "github": "https://github.com/BAI-LAB/MemoryOS",
            "install": "pip install git+https://github.com/BAI-LAB/MemoryOS",
        },
        "entry_class": {
            "name": "MemoryosAdapter",
            "file": _MOS_FILE,
            "line": 163,
        },
        "methods": _MEMORYOS_METHODS,
        "middleware_deps": [
            "EmbeddingService（统一传 embedding_model_name=bge-small-zh-v1.5）",
            "MemoryosWrapper（adapters/memoryos_adapter.py:80，本地兼容层 + reset_all 兜底）",
        ],
        "storage": "内置 faiss（三层 short/mid/long）+ LLM 抽取",
        "venv": ".venv-memoryos",
        "known_issues": [
            "faiss 距离度量待确认是否 Cosine（gemini A.4）",
            "长 turn 切分策略未配（gemini A.2）",
        ],
    },
    "graphiti": {
        "name": "Graphiti",
        "sdk": {
            "package": "graphiti-core",
            "version": "0.18.9",
            "github": "https://github.com/getzep/graphiti",
            "install": "pip install graphiti-core==0.18.9",
        },
        "entry_class": {
            "name": "GraphitiAdapter",
            "file": _GRA_FILE,
            "line": 45,
        },
        "methods": _GRAPHITI_METHODS,
        "middleware_deps": [
            "_RealGraphitiWrapper（middleware/graphiti_compat.py，持久 event loop 解决 async/sync 桥）",
            "make_st_embedder（统一 sentence-transformers）",
            "make_real_graphiti_client_factory（自定义 LLMClient 接 GEMINI_PROXY chat.completions）",
        ],
        "storage": "FalkorDB（QNAP 16379，graph_db + 向量混合检索）",
        "venv": ".venv-graphiti",
        "known_issues": [
            "async/sync bridge 跨调用 event loop 必须保持单一（已修，见 graphiti_compat.py）",
        ],
    },
    "hindsight": {
        "name": "Hindsight（recall/reflect 双模式）",
        "sdk": {
            "package": "hindsight-all",
            "version": "0.8.1",
            "github": "https://github.com/vectorize-io/hindsight",
            "install": "pip install hindsight-all==0.8.1",
        },
        "entry_class": {
            "name": "HindsightAdapter",
            "file": _HS_FILE,
            "line": 77,
        },
        "methods": _HINDSIGHT_METHODS,
        "middleware_deps": [
            "SidecarStore（写前查重幂等 + turn_id 兜底）",
            "EmbeddingService（reflect 辅路 source_embedding 统一空间）",
            "评测标准 env 五件套（中文 embedding/reranker + 关 auto-consolidation，见 HINDSIGHT_VERIFIED_FACTS.md）",
        ],
        "storage": "pg0（内嵌 PostgreSQL，embedded 自包含无外部服务；bank_id={user}__{mode} 物理隔离双身份）",
        "venv": ".venv-hindsight",
        "known_issues": [
            "reflect 的 agent 多轮 tool call 撞 gemini-3 thought_signature（proxy 不透传）→ reflect stage 族内降 gemini-2.5-flash（per-stage env），retain 仍统一 gemini-3；修 proxy 透传后可回归",
            "auto-consolidation 默认开启且产英文 observation（不带 turn_id）→ 评测 env 已关闭，consolidation 只走显式 API",
            "默认 embedding(bge-small-en)/reranker(ms-marco) 是英文模型，中文必换（A/B 实测英文 reranker 把正确答案压到 #4）",
        ],
    },
    "reme": {
        "name": "ReMe（agentic 检索·晚绑定变体）",
        "sdk": {
            "package": "reme-ai",
            "version": "0.3.1.10",
            "github": "https://github.com/agentscope-ai/ReMe",
            "install": "pip install reme-ai agentscope==1.0.20",
        },
        "entry_class": {
            "name": "RemeAdapter",
            "file": "src/kidsbench/adapters/reme_adapter.py",
            "line": 96,
        },
        "methods": [
            _method("write", "abstract", "src/kidsbench/adapters/reme_adapter.py", 175,
                    "只进缓存（不调 LLM）+ message_time→turn_id 映射；ReMe 语义单位是对话批"),
            _method("flush", "abstract", "src/kidsbench/adapters/reme_adapter.py", 193,
                    "真写入点：缓存批 → summarize_memory（LLM 多轮工具循环抽取）"),
            _method("read", "abstract", "src/kidsbench/adapters/reme_adapter.py", 217,
                    "agentic 检索：retrieve_memory → 合成 answer + retrieved_nodes"),
            _method("clear", "abstract", "src/kidsbench/adapters/reme_adapter.py", 270,
                    "delete_all 全库清（评测串行无碰撞）+ 缓存/映射清"),
            _method("consolidate", "overridable", "src/kidsbench/adapters/reme_adapter.py", 288,
                    "语义整理已在 summarize 内，无独立 consolidate"),
            _method("get_dependencies", "abstract", "src/kidsbench/adapters/reme_adapter.py", 388, ""),
            _method("get_stats", "abstract", "src/kidsbench/adapters/reme_adapter.py", 413, ""),
            _method("get_capability_profile", "abstract", "src/kidsbench/adapters/reme_adapter.py", 422, ""),
        ],
        "middleware_deps": [
            "中文 prompt patch（PromptHandler.prompt_format 单点注入，零 fork；vector 路径无 _zh prompt 默认抽英文记忆）",
            "embedding_shim（bge-small-zh-v1.5 包成 OpenAI 端点，ReMe 仅支持 API 形态 embedding）",
        ],
        "storage": "local 纯 Python 向量后端（JSONL，零外部服务）；user_name 逻辑隔离",
        "venv": ".venv-reme",
        "known_issues": [
            "vector 路径记忆 prompt 无中文版（_zh）→ 默认抽英文记忆，需 monkey patch 注入中文指令（实测注入后完全中文化）",
            "deepseek 偶发 rate limit 中断长跑批 → --resume 续跑（实测 91→149 续跑零损）",
            "token usage 不上报（return_dict 无 usage 字段），cost_token 计 0；agentscope 必须钉 1.0.20",
        ],
    },
}


# ============================================================
# 3 个记忆系统元信息
# ============================================================

MEMORY_SYSTEMS: dict = {
    "mem0_storage": {
        "name": "Qdrant (mem0 后端)",
        "kind": "vector_db",
        "introduction": {
            "tldr": "mem0 是「记忆管理层」，Qdrant 是「向量仓库」。mem0 决定记什么 / 怎么更新 / 何时取出，Qdrant 把记忆存成向量、按语义相似度快速找回。",
            "problem": "LLM 本身没有真正的长期记忆，一次对话结束后除非把历史重塞 prompt，模型「不知道以前发生过什么」。",
            "mechanism": [
                "用户说一句话",
                "mem0 用 LLM 判断里面有没有值得长期保存的事实",
                "把这条事实转成 embedding 向量",
                "向量入 Qdrant collection",
                "下次用户问相关问题时，从 Qdrant 按语义相似度召回",
                "把召回的记忆放回 prompt，让模型「像是记得」",
            ],
        },
        "schema": {
            "collection": "kidsbench_eval_bge",
            "dim": 512,
            "distance": "Cosine",
            "fields": ["id", "vector", "payload.text", "payload.user_id", "payload.metadata"],
        },
        "deployment": "本地 :6333（mem0 SDK 内置 / docker 可选）",
        "real_time_stats": False,
        "stats_source": "B0 阶段从最近 run 的 results.jsonl 抽 stats",
    },
    "memoryos_storage": {
        "name": "MemoryOS 三层 + faiss",
        "kind": "hierarchical_memory",
        "introduction": {
            "tldr": "把 AI 记忆做成「操作系统内存管理」一样的分层体系。MemoryOS 负责分层、更新、调度；FAISS 在每一层做向量检索。",
            "problem": "上下文窗口有限 + 不同时间维度的信息需要不同的保鲜策略：刚发生的要原汁原味，长期的要凝练。",
            "mechanism": [
                "Storage：内容按价值 / 时间分到三层",
                "Updating：短期→中期→长期的迁移与凝练（LLM 整理）",
                "Retrieval：跨层混合召回 + FAISS 向量索引",
                "Generation：召回片段塞回 prompt",
            ],
            "layers": [
                {
                    "level": "短期记忆 Short-Term",
                    "analogy": "当前聊天窗口",
                    "content": "最近几轮 raw turn",
                },
                {
                    "level": "中期记忆 Mid-Term",
                    "analogy": "最近会话摘要 / 工作缓存",
                    "content": "一段时间内的主题、任务、对话链（LLM 合并）",
                },
                {
                    "level": "长期记忆 Long-Term",
                    "analogy": "个人档案 / 长期知识库",
                    "content": "用户偏好、稳定事实、长期经历",
                },
            ],
        },
        "schema": {
            "short_term": "最近 N 轮 raw turn",
            "mid_term": "短期合并后的中段记忆 + LLM 总结",
            "long_term": "高价值长期事实 + faiss 向量索引",
        },
        "deployment": "本地持久化目录（per-user 一份）",
        "real_time_stats": False,
        "stats_source": "B0 阶段从最近 run 的 results.jsonl 抽 stats",
    },
    "graphiti_storage": {
        "name": "FalkorDB (graphiti 图谱后端)",
        "kind": "graph_db",
        "introduction": {
            "tldr": "Graphiti 把数据结构化成「实体（节点）+ 关系（边）」的知识图谱，并按时间维度追踪事实的变化。FalkorDB 是底层图数据库。",
            "problem": "向量检索只看「内容像不像」，看不见「实体之间的关系」和「事实随时间怎么变」。",
            "mechanism": [
                "新一轮对话 → 一个 episode",
                "用 LLM 从 episode 抽取实体 + 关系（人 / 物 / 事件 / 属性）",
                "实体 / 关系入 FalkorDB 图谱（带时间戳）",
                "事实变化时不删旧的，加一条「失效时间」（双时间维度）",
                "查询时混合检索：embedding 召回 + 图遍历 + 时间过滤",
            ],
            "key_diff": "不只记事实本身，还记事实在不同时间点的变化（例：「团子」3 月还是小猫，5 月长成大猫，两个事实都在图里）",
        },
        "schema": {
            "node_labels": ["Episode", "Entity"],
            "edge_labels": ["RELATES_TO", "MENTIONS"],
            "scoped_by": "group_id（= user_id）",
        },
        "deployment": "QNAP TS-X65 :16379（已有，multica 同机）",
        "real_time_stats": True,
        "stats_source": "B0 阶段直连 FalkorDB 拉 GRAPH.LIST + Cypher COUNT",
    },
    "hindsight_storage": {
        "name": "Hindsight 四路检索 + pg0",
        "kind": "hybrid_memory",
        "introduction": {
            "tldr": "一个会「反思」的记忆系统：不只「存下来、查出来」，还能基于经历总结出「信念」。查询有两档：recall 快查（不调大模型，便宜快速）和 reflect 深思（调大模型做合成）——同一系统提供早绑定/晚绑定两个范式数据点，是 KidsBench 范式制图的关键旋钮。",
            "problem": "早绑定系统写入时就把对话压缩成事实，可能过早丢失「当时不重要、后来才重要」的信息；纯晚绑定每次读取都很贵。Hindsight 把两条路都留着，让使用方按需选择。",
            "mechanism": [
                "Retain（记住）：写入时 LLM 同步抽取事实/实体/关系/时间，入 pg0（内嵌 PostgreSQL）",
                "Recall（快查）：向量 + BM25 + 图（实体/时序/因果）四路并行召回 → RRF 融合 → bge-reranker 重排，全程不调 LLM",
                "Reflect（深思）：LLM agent 检索后做深度分析，合成「心智模型」（如：这孩子遇挫折容易自我否定）",
                "记忆三分类：World facts（客观事实）/ Experiences（经历）/ Mental models（反思得出的信念）",
            ],
            "key_diff": "「事实 vs 信念」分离，正好对应 K12 的「客观知识 vs 主观感受」；recall/reflect 双读取路径 = 同一系统贡献早/晚绑定两个 Pareto 对照点（smoke 实测：read 成本 0 tok/1.3s vs 43k tok/47s，correct 持平）",
        },
        "schema": {
            "tables": ["banks", "documents", "memory_units", "entities", "unit_entities", "memory_links", "mental_models"],
            "fact_types": ["world", "experience", "opinion", "observation"],
            "scoped_by": "bank_id（= user_id + __recall/__reflect 模式后缀，物理隔离）",
        },
        "deployment": "embedded pg0（内嵌 PostgreSQL，~/.pg0/instances/，跟随 harness 进程，无外部服务）",
        "real_time_stats": False,
        "stats_source": "B0 阶段从最近 run 的 results.jsonl 抽 stats",
    },
    "reme_storage": {
        "name": "ReMe agentic 检索 + local 向量库",
        "kind": "agentic_memory",
        "introduction": {
            "tldr": "阿里出品（agentscope-ai）的记忆框架。最大特点是「读取时让一个小 agent 多轮工具调用去翻记忆」——不是一次查完，而是边查边想、反复检索，最后综合成答案。中文原生支持，存储是纯 Python 本地向量库（零外部服务）。",
            "problem": "一次性向量检索（查一次拿 top-k）对复杂问题不够——可能第一次没查到关键的，需要换个角度再查。ReMe 用 agent 自主决定查几次、怎么查。",
            "mechanism": [
                "Summarize（写入）：把一批对话喂给 LLM，自动提炼成事实型记忆（用户偏好/任务经验），入本地向量库",
                "Retrieve（读取）：一个 agent 多轮工具循环——查记忆 → 看够不够 → 不够换角度再查 → 综合成回答（晚绑定变体）",
                "记忆带 message_time（写入时从对话时间标注抄），KidsBench 用它反查 turn_id 做溯源",
                "中文 prompt 注入：vector 路径默认抽英文记忆，patch 后完全中文化",
            ],
            "key_diff": "「agentic 多轮检索」是它和 mem0（一次 hybrid rerank）/ hindsight（四路并行一次融合）的范式区别——召回更全（实测 recall 0.81）但读取慢（agent 工具循环）。与 hindsight-reflect 同属晚绑定家族，机制不同（多轮检索 vs 一次合成），构成范式内对照",
        },
        "schema": {
            "tables": ["local vector store（JSONL，纯 Python）", "memory_node（personal/task/tool 三类）"],
            "fact_types": ["personal（用户偏好）", "task（任务经验）", "tool（工具记忆）"],
            "scoped_by": "user_name（逻辑隔离，delete_all 全库清）",
        },
        "deployment": "local 纯 Python 向量后端（JSONL 文件，零外部服务）；embedding 经本地 shim 对齐 bge-small-zh",
        "real_time_stats": False,
        "stats_source": "B0 阶段从最近 run 的 results.jsonl 抽 stats",
    },
}


@router.get("")
def get_architecture() -> dict:
    """返回完整架构索引（contract + adapter + memory system 元信息）。"""
    return {
        "contract": CONTRACT,
        "adapters": ADAPTERS,
        "memory_systems": MEMORY_SYSTEMS,
        "embedding_model": {
            "name": "BAAI/bge-small-zh-v1.5",
            "dim": 512,
            "size_mb": 192,
            "max_tokens": 512,
            "is_asymmetric": True,
        },
        "llm_model": {
            "name": "gemini-3.5-flash",
            "provider": "GEMINI_PROXY",
            "endpoint": "http://23.226.135.149:4000/v1",
            "reasoning_effort": "minimal (default for adapter LLM calls)",
        },
    }


@router.get("/contract")
def get_contract() -> dict:
    """返回 MemoryAdapter ABC 契约信息。"""
    return CONTRACT


@router.get("/adapter/{adapter_name}")
def get_adapter(adapter_name: str) -> dict:
    if adapter_name not in ADAPTERS:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"unknown adapter: {adapter_name}")
    return ADAPTERS[adapter_name]


@router.get("/memory/{memory_name}")
def get_memory_system(memory_name: str) -> dict:
    key = memory_name if memory_name in MEMORY_SYSTEMS else f"{memory_name}_storage"
    if key not in MEMORY_SYSTEMS:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"unknown memory system: {memory_name}")
    return MEMORY_SYSTEMS[key]
