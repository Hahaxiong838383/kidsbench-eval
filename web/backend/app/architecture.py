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
    "letta": {
        "name": "Letta（MemGPT archival 直插）",
        "sdk": {
            "package": "letta + letta-client",
            "version": "0.16.8 / 1.12.1",
            "github": "https://github.com/letta-ai/letta",
            "install": "pip install letta letta-client（server 部署见 scripts/setup_letta_server.sh）",
        },
        "entry_class": {
            "name": "LettaAdapter",
            "file": "src/kidsbench/adapters/letta_adapter.py",
            "line": 57,
        },
        "methods": [
            _method("write", "abstract", "src/kidsbench/adapters/letta_adapter.py", 132,
                    "archival passage 直插，tags=[turn_id] 做 native 溯源（最干净 1:1）"),
            _method("flush", "abstract", "src/kidsbench/adapters/letta_adapter.py", 150,
                    "passage insert 同步落库（含 embedding），无需 flush"),
            _method("read", "abstract", "src/kidsbench/adapters/letta_adapter.py", 156,
                    "archival search → Result(content/tags/id)，tags 原样回传"),
            _method("clear", "abstract", "src/kidsbench/adapters/letta_adapter.py", 198,
                    "删 agent（organization 隔离）整清 archival，下题重建"),
            _method("consolidate", "overridable", "src/kidsbench/adapters/letta_adapter.py", 218,
                    "archival 即时可检索，无独立 consolidate"),
            _method("get_dependencies", "abstract", "src/kidsbench/adapters/letta_adapter.py", 251, ""),
            _method("get_stats", "abstract", "src/kidsbench/adapters/letta_adapter.py", 273, ""),
            _method("get_capability_profile", "abstract", "src/kidsbench/adapters/letta_adapter.py", 282, ""),
        ],
        "middleware_deps": [
            "letta server（pg0 嵌入式 Postgres + deepseek custom provider，setup_letta_server.sh 一键起）",
            "embedding_shim（bge-small-zh-v1.5，embedding_config 直传指向，512维对齐评测标准）",
        ],
        "storage": "Postgres（pg0 嵌入式）archival passage 向量库 + memory_blocks；agent/organization 级隔离",
        "venv": ".venv-letta",
        "known_issues": [
            "0.16 server 只支持 Postgres（无 SQLite 分支）→ pg0 嵌入式 + 手动 create_all + 补 message_seq_id sequence（8 坑全记 setup_letta_server.sh）",
            "deepseek base provider 与 v4 系列不兼容（空模型列表）→ 必走 custom openai provider 指向 api.deepseek.com",
            "走 archival 直插路径（不用 agent 自管理回答），评测协议回答端用统一模型保一致",
        ],
    },
    # ===== 三个参照基线（不是真实记忆系统，是评测的科学对照组）=====
    "nomemory": {
        "name": "NoMemory（地板参照·无记忆对照组）",
        "is_baseline": True,
        "sdk": {"package": "（内置）", "version": "—",
                "github": "src/kidsbench/adapters/nomemory.py",
                "install": "无需安装（评测框架内置）"},
        "entry_class": {"name": "NoMemoryAdapter",
                        "file": "src/kidsbench/adapters/nomemory.py", "line": 27},
        "methods": [
            _method("write", "abstract", "src/kidsbench/adapters/nomemory.py", 43,
                    "空操作——什么都不存（这就是它的定义：完全没有记忆）"),
            _method("read", "abstract", "src/kidsbench/adapters/nomemory.py", 50,
                    "永远返回空——不召回任何历史，模型只能看当前 prompt（场景+当场对话+触发）"),
            _method("clear", "abstract", "src/kidsbench/adapters/nomemory.py", 57, "空操作"),
            _method("get_capability_profile", "abstract", "src/kidsbench/adapters/nomemory.py", 75, ""),
        ],
        "middleware_deps": [],
        "storage": "无（这正是它的意义——对照『没有记忆会怎样』）",
        "venv": "（内置）",
        "known_issues": [
            "⚠️ 这不是一个『差的记忆系统』，是科学对照组——衡量『不靠记忆能答对多少』",
            "它答对的题 = 答案泄露了（藏在场景/当场对话里或能被常识猜中）→ 触发题库泄露告警",
            "它的回避率高才正常（没记忆诚实说不知道，比瞎猜强）",
        ],
    },
    "fullhistory": {
        "name": "FullHistory（理论上限参照·全历史暴力塞）",
        "is_baseline": True,
        "sdk": {"package": "（内置）", "version": "—",
                "github": "src/kidsbench/adapters/fullhistory.py",
                "install": "无需安装（评测框架内置）"},
        "entry_class": {"name": "FullHistoryAdapter",
                        "file": "src/kidsbench/adapters/fullhistory.py", "line": 28},
        "methods": [
            _method("write", "abstract", "src/kidsbench/adapters/fullhistory.py", 46,
                    "原样存下每条对话（不抽取、不压缩、不向量化）"),
            _method("read", "abstract", "src/kidsbench/adapters/fullhistory.py", 53,
                    "把全部历史原文一股脑返回——不检索、不筛选，全给模型（溯源 100% 因为原文全在）"),
            _method("clear", "abstract", "src/kidsbench/adapters/fullhistory.py", 75, "清空历史列表"),
            _method("get_capability_profile", "abstract", "src/kidsbench/adapters/fullhistory.py", 93, ""),
        ],
        "middleware_deps": [],
        "storage": "内存里存全部对话原文（无向量库）",
        "venv": "（内置）",
        "known_issues": [
            "⚠️ 这不是『最强记忆系统』，是不现实的暴力上限——把所有历史塞进 prompt",
            "现实不可用：历史一长 token 爆炸、贵、超上下文窗口；它存在只为校准『题目本身可不可答』",
            "它都答不对的题 = 题目或判分命题有问题（不是记忆系统的锅）→ 触发上限失守告警",
        ],
    },
    "oracle": {
        "name": "Oracle（判分天花板参照·完美召回理想化）",
        "is_baseline": True,
        "sdk": {"package": "（内置）", "version": "—",
                "github": "src/kidsbench/adapters/oracle.py",
                "install": "无需安装（评测框架内置）"},
        "entry_class": {"name": "OracleAdapter",
                        "file": "src/kidsbench/adapters/oracle.py", "line": 34},
        "methods": [
            _method("write", "abstract", "src/kidsbench/adapters/oracle.py", 54,
                    "记录所有 turn（但读取时只挑标准答案那几条）"),
            _method("read", "abstract", "src/kidsbench/adapters/oracle.py", 61,
                    "作弊式完美召回——直接返回该题标注的 gold 记忆（标准答案那几句），假设检索 100% 准"),
            _method("clear", "abstract", "src/kidsbench/adapters/oracle.py", 88, "清空"),
            _method("get_capability_profile", "abstract", "src/kidsbench/adapters/oracle.py", 105, ""),
        ],
        "middleware_deps": ["每题前注入该题 gold_memory_ids 对应的标准答案"],
        "storage": "无独立存储（直接用题目标注的 gold）",
        "venv": "（内置）",
        "known_issues": [
            "⚠️ 这不是真实系统，是理想化参照——假设『检索 100% 完美、只给最该想起的那几句』",
            "它是『最小充分信息』参照（只喂标准答案），不是绝对上限",
            "真实系统超过 Oracle = 多召回的周边上下文反而帮模型答得更好（mem0/memoryos 实测超过它）",
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
    "letta_storage": {
        "name": "Letta MemGPT archival + pg0",
        "kind": "agentic_memory",
        "introduction": {
            "tldr": "MemGPT 论文的产品化（23K 星）。核心思想是让 AI agent 像操作系统管内存一样自主管理记忆——决定什么存进长期记忆、什么留在工作区。KidsBench 走它的 archival（长期向量库）直插路径，溯源是所有系统里最干净的（每条记忆带 turn 标签精确 1:1）。",
            "problem": "传统记忆系统是被动存取，MemGPT 让 agent 主动管理——但 agent 自管理有不确定性。KidsBench 评测走 archival 直插绕开不确定性，只测它的存储/检索能力。",
            "mechanism": [
                "archival passage 写入：每条记忆带 tags（KidsBench 存 turn_id），入 pg0 向量库",
                "search 检索：语义查 archival，返回的 passage 原样带回 tags → turn_id 精确溯源",
                "memory_blocks：人设/用户画像的工作区记忆（KidsBench 评测不主用）",
                "回答端：KidsBench 拿 passage 给统一回答模型，不用 letta agent 自己答（保评测协议一致）",
            ],
            "key_diff": "「agent 自主管理记忆」是 MemGPT 范式标志，与 memoryos 的分层存储构成范式内对照；archival 的 tags 溯源是六系统最干净的 native 1:1（实测 149/149 全命中）",
        },
        "schema": {
            "tables": ["passages（archival 向量库）", "blocks（memory_blocks 工作区）", "agents", "messages"],
            "fact_types": ["archival passage（长期记忆）", "memory_block（human/persona 工作区）"],
            "scoped_by": "agent_id / organization_id（每 user 一个 agent，删 agent 整清）",
        },
        "deployment": "pg0 嵌入式 Postgres（server 模式，setup_letta_server.sh 一键起）；embedding 经本地 shim",
        "real_time_stats": False,
        "stats_source": "B0 阶段从最近 run 的 results.jsonl 抽 stats",
    },
    # ===== 三个参照基线（评测的科学对照组，不是真实记忆系统）=====
    "nomemory_storage": {
        "name": "NoMemory · 地板参照",
        "kind": "参照基线（非真实系统）",
        "is_baseline": True,
        "introduction": {
            "tldr": "它根本没有记忆——这正是它的用途。每道题它只能看当前画面（场景+当场对话+触发输入），完全不召回任何历史。它是『不靠记忆能答对多少』的地板线。把它当一把尺子，不是当一个产品。",
            "problem": "评测记忆系统，得先知道『不用记忆能蒙对多少』。如果一道题没记忆也能答对，那这题考不出记忆的价值。NoMemory 就是这把标尺。",
            "mechanism": [
                "write：什么都不存（定义上就没有记忆）",
                "read：永远返回空，模型手里只有当前 prompt",
                "判分：它答对的题 = 答案泄露了（藏在场景里或能被常识猜中），系统自动告警",
            ],
            "key_diff": "⚠️ 防误解：它不是『很差的记忆系统』，是科学对照组。它回避率高、分数低才正常——那说明题目真的需要记忆才能答。",
        },
        "schema": {"tables": ["无"], "fact_types": ["无"], "scoped_by": "无"},
        "deployment": "评测框架内置（src/kidsbench/adapters/nomemory.py）",
        "real_time_stats": False, "stats_source": "从最近 run 抽 stats",
    },
    "fullhistory_storage": {
        "name": "FullHistory · 理论上限参照",
        "kind": "参照基线（非真实系统）",
        "is_baseline": True,
        "introduction": {
            "tldr": "它把全部历史对话原文一股脑塞进 prompt——不检索、不压缩、不挑选，全给模型。这是『信息全给到的理论上限』。但它在现实里不可用（历史一长 token 就爆炸、又贵又超窗口），存在只为当上限标尺。",
            "problem": "记忆系统的本事是『从一堆历史里挑出该想起的那几条』。要知道它挑得好不好，得有个『全都给它、不用挑』的对照——那就是天花板。记忆系统越接近 FullHistory 越好，但目标是用极小的检索代价接近它。",
            "mechanism": [
                "write：原样存下每条对话（不做任何加工）",
                "read：把全部历史原文返回，模型自己从里面找（溯源 100%，因为原文全在）",
                "判分：它都答不对的题 = 题目或判分命题有问题（不是记忆系统的锅），系统告警",
            ],
            "key_diff": "⚠️ 防误解：它不是『最强记忆系统』，是不现实的暴力基线。真实产品不可能每次把所有历史塞进去——那正是记忆系统要解决的问题。",
        },
        "schema": {"tables": ["内存中的对话原文列表"], "fact_types": ["原文（不抽取）"], "scoped_by": "user_id"},
        "deployment": "评测框架内置（src/kidsbench/adapters/fullhistory.py）",
        "real_time_stats": False, "stats_source": "从最近 run 抽 stats",
    },
    "oracle_storage": {
        "name": "Oracle · 判分天花板参照",
        "kind": "参照基线（非真实系统）",
        "is_baseline": True,
        "introduction": {
            "tldr": "它『作弊』——每道题直接拿到标准答案那几句记忆（题目预先标注好的 gold），假设检索 100% 完美。它衡量的是『如果记忆系统一点不出错，能拿多少分』——也就是判分管线本身的天花板。",
            "problem": "如果连『完美喂给标准答案』都答不对/判不对，那是判分或题目的问题，不是记忆系统的问题。Oracle 把『记忆系统的锅』和『判分/题目的锅』分开。",
            "mechanism": [
                "write：记下所有 turn",
                "read：直接返回该题标注的 gold 记忆（标准答案那几句），跳过真实检索",
                "意义：它是『最小充分信息』参照——只喂最该想起的那几句，不多不少",
            ],
            "key_diff": "⚠️ 防误解：Oracle 不是某个产品/真实系统，是理想化参照。有意思的是真实系统能超过它（mem0/memoryos 实测）——因为真实系统多召回的周边上下文反而帮模型组织得更好，说明 Oracle 是『最小充分』而非『绝对上限』。",
        },
        "schema": {"tables": ["无独立存储"], "fact_types": ["题目标注的 gold"], "scoped_by": "per-question gold 注入"},
        "deployment": "评测框架内置（src/kidsbench/adapters/oracle.py）",
        "real_time_stats": False, "stats_source": "从最近 run 抽 stats",
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
