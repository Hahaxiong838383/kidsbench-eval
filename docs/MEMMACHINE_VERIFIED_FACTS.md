# MemMachine 核实事实（Phase 0 产出）

> 日期：2026-06-12 ｜ 版本锁定：源码 main（uv workspace：packages/common+server+client，pip -e 装通）｜ .venv-memmachine（py3.12）
> 方法：源码 agent 扫描（/tmp/kb-survey/memmachine）+ 本机实测（全 SQLite 配置真跑，含重启持久化验证）
> 实测脚本：`scripts/phase0_memmachine_verify.py`
> 背景：team 三发评审全票第二候选（真值保存范式：原始对话+句级索引，抗反馈环路腐蚀对照组）。
> 仓库活跃（⭐3112，前日仍 push，cc gh 核实）。

## 红绿灯总表

| # | 核实点 | 结果 | 证据 |
|---|---|---|---|
| 1 | **部署重量【最大疑虑】** | 🟢 **解除** | docker-compose 默认 PG+Neo4j 仅是推荐形态；**全 SQLite 嵌入式路径实测通**：episodic=event backend + `sqlite_vector_store`(usearch) + sqlite 关系库，零 docker/零外部服务（database_conf.py 支持 SQLITE/SQLITE_VEC 枚举） |
| 2 | **LLM 注入【一票否决】** | 🟢 | `language_models.*.provider: openai-chat-completions` + base_url → deepseek 实测；启动自带 LLM/embedder 双 validate 真调 |
| 3 | **中文可用【一票否决】** | 🟢 | 分句正则显式含 CJK 标点 `？！。`（utils.py:163-191）+ segmenter 分隔符含全角逗号顿号（text_segmenter.py:31-67）；ID 校验 `\p{L}\p{N}`Unicode 友好（spec.py:176-182）；实测中文写入+语义检索命中 |
| 4 | **物理清场【一票否决】** | 🟢 | `POST /projects/delete` 204 + 日志可见 history ids 真删；删后 search 抛 `SessionDeletedError`（语义正确，adapter 每题换 project_id） |
| 5 | 溯源 | 🟢 **native** | MemoryMessage.metadata `{"turn_id":"t_001"}` 写入 → 检索 episode 原样回传 + score 暴露（0.56）；semantic feature 另有 citations=episode ids |
| 6 | 虚拟时钟 | 🟢 | MemoryMessage.timestamp（ISO8601）注入 7 天前 → episode created_at 原样落地 `2026-06-05T...Z`，排序用此字段（episodic_memory.py:518） |
| 7 | embedding 注入 | 🟢 | `embedders.*.provider: openai` + base_url → 本地 shim（bge-small-zh 512d）实测打通（写入时 ingest_embed 真调 18230） |
| 8 | 写入幂等性 | ⚠️ 非幂等 | 同内容写 2 次检索出 2 条（实测）→ adapter turn_id 写前查重必做（同 hindsight 教训） |
| 9 | STM/LTM 去重语义 | 📌 | episode 同时写 STM+LTM（episodic_memory.py:233-235）；search 回包 **STM 优先去重**——LTM 列表对仍在 STM 的条目显示空。重启 server 后 STM=0、LTM 独立召回成功（向量路径实锤）。adapter read 取 STM∪LTM 并集即可 |
| 10 | token 计量 | 🟡 | embedding usage 走 Prometheus counter（openai_embedder.py:308-312）；LLM usage 内部记录无 API 暴露 → 榜单「未上报」机制兼容 |
| 11 | 后台自动行为 | 🟢 可控 | semantic consolidation 由写入触发 + `consolidation_threshold` 控制（可设 0 关闭），无定时 worker |

## 工程事实（接入要记住的）

1. **安装**：`pip install -e packages/common -e packages/server -e packages/client`（uv workspace，pip 直装通）。
2. **启动**：`MEMORY_CONFIG=<cfg.yml> memmachine-server`；config 从 env `MEMORY_CONFIG` > `~/.config/memmachine/cfg.yml` > `./cfg.yml` 解析（mcp.py:327）。
3. **评测标准配置**（实测全通，存档 /tmp/kb-phase0-memmachine/cfg.yml 模式）：
   - episodic long_term：`backend: event` + `segmenter: {type: text}` + `deriver: {type: sentence_text}`（句级索引开关在这）+ `vector_store: sqlite_vector_store(usearch)`
   - semantic：`storage_backend: vector_store` + `vector_dimensions: 512`
   - 全部 databases 走 sqlite provider
4. **API（v2）**：写 `POST /api/v2/memories`（messages[]: content/producer/role/timestamp/metadata）；读 `POST /api/v2/memories/search`（org_id/project_id/query/top_k）；清 `POST /api/v2/projects/delete`。
5. 写 0.3s/2 条、读 <0.1s（不含 LLM）——六家+三家里读端最快档。
6. semantic（profile 类）记忆与 episodic 并存：评测主测 episodic 真值保存；semantic 抽取依赖 LLM、有 consolidation，Phase 2 先关或单列。

## 给 Phase 1/2 的输入

- Phase 1：固化 `scripts/setup_memmachine_server.sh`（venv + cfg.yml 模板 + shim 依赖检查）
- Phase 2 adapter：write=POST /memories（metadata.turn_id + timestamp=虚拟时间，写前查重）；read=search 取 STM∪LTM 并集（带 score）；clear=delete project + 每题独立 project_id；flush=写路径同步无需额外动作
- Phase 2 契约测试必做（codex 对抗审 P0 采纳）：STM∪LTM 并集在**运行中**（非重启后）的排序/去重/冲突语义专项测试；清场后 vector 表 orphan 残留审计
- 范式登记：**真值保存（原文+句级索引，immutable ledger）**，主场 = T3 矛盾更新（无损上下文对照）+ 脏数据/抗幻觉类
