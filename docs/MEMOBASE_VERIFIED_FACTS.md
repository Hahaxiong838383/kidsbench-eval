# Memobase 核实事实（Phase 0 产出）

> 日期：2026-06-12 ｜ 版本锁定：server **0.0.42**（源码跑）+ client **0.0.27**（PyPI）｜ .venv-memobase（py3.12）
> 方法：源码 agent 扫描（/tmp/kb-survey/memobase）+ 本机实测两轮（deepseek 注入真跑）
> 实测脚本：`scripts/phase0_memobase_verify.py`（中文写入→flush→画像→清场全链路）+ 幂等补测
> 背景：team 三发评审全票第一候选（画像中心范式，K12 长期画像格子唯一候选）。
> ⚠️ 仓库 2026-01-11 起停更 5 个月（cc gh 核实），但依赖零腐烂、py3.12 一把装通。

## 红绿灯总表

| # | 核实点 | 结果 | 证据 |
|---|---|---|---|
| 1 | **LLM 注入【一票否决】** | 🟢 | config.yaml `llm_base_url/best_llm_model` → deepseek 实测；启动自带 `llm_sanity_check` 真调验证 |
| 2 | **中文可用【一票否决】** | 🟢 **原生最优** | `language: Literal["en","zh"]` 一等公民配置（env.py:94）+ 全套 zh prompts（zh_extract_profile 等 7+ 文件）。实测画像纯中文：「兴趣爱好/宠物: 养了一只布偶猫，名叫团子，2岁，喜欢吃冻干三文鱼」。**六家+三家里唯一零 patch 中文** |
| 3 | **物理清场【一票否决】** | 🟢 | delete_user 后读取报错；表结构 (user_id, project_id) 复合外键全隔离（database.py:242-506） |
| 4 | 异步 flush 时序 | 🟢 **官方解决** | `flush(sync=True)` 同步等待（实测 14.5s），返回后画像立刻可读——gemini 预判的最大风险被官方 API 化解 |
| 5 | 虚拟时钟 | 🟢 | Message 级 `created_at`（字符串 "YYYY-MM-DD HH:MM:SS"）注入 7 天前 → 画像带 `[提及于2026-06-05]` 原样落地 |
| 6 | token 计量 | 🟡 | LLM usage 内部记录（openai_model_llm.py:21-24）+ Billing 表 + `/project/billing` 端点；**无 per-call API 暴露** → adapter 走 billing 差值或标「未上报」 |
| 7 | 溯源链 | 🔴→🟢wrapped | **blob→profile 无原生关联**（UserProfile/UserEvent 表无 blob_id；BufferZone 关联 flush 后即删）→ adapter 层 wrapped：按 `[提及于date]` 时间标记反查 turn（与 ReMe message_time 反查同级） |
| 8 | 写入幂等性 | 🟡 | blob 层无去重（blob.py:9-25 直接 add；buffer.py:124 有 FIXME 自认）；**但画像层 LLM merge 兜住**——实测同 blob 写 2 次 flush 后画像仍 1 条不重复。adapter 仍建议查重（省 LLM 成本 + 防 event 重复） |
| 9 | buffer 自动行为 | 🟢 可控 | 唯一自动触发 = buffer token 超 `max_chat_blob_buffer_token_size`（默认 1024，**同步**触发非后台）；评测配置抬到 8192（实测生效）+ 逐题显式 flush ⇒ 时序完全可控。`buffer_flush_interval` 只是后台任务锁超时不是定时器（buffer_background.py:124） |
| 10 | event 检索 | ⚠️ 依赖 embedding | `enable_event_embedding=false` 时 search_event 直接 NOT_IMPLEMENTED（event.py:224-232）无降级 → **Phase 2 必须接本地 shim**（bge-small-zh，512d，OpenAI 兼容直配） |

## 工程事实（接入要记住的）

1. **server 无法 pip install**：pyproject 未配 packages（flat-layout 多包冲突），官方只走 Docker。
   绕法（实测）：venv 装依赖清单 + api/ 目录源码直跑 `uvicorn api:app`。
2. **部署形态**：FastAPI + Postgres(pgvector) + Redis 三件套。本机评测组合（实测全通）：
   pg0 嵌入式 PG（port 5434 + CREATE EXTENSION vector）+ brew redis（port 6399）+ 源码 uvicorn（8019）。
   环境变量：`DATABASE_URL` / `REDIS_URL` / `ACCESS_TOKEN`（API 鉴权 token）/ `PROJECT_ID`。
3. **config.yaml 从 CWD 加载**；⚠️ server 启动把 llm_api_key 全文打进日志——生产部署要管日志级别。
4. **K12 现成资产**：`example_config/profile_for_education`（5 topics/21 sub_topics：basic_info/academic_profile/learning_preferences/progress_tracking/engagement_metrics）+ `profile_for_companion`，经 `overwrite_user_profiles` 启用。默认 zh 抽取已产出「教育背景/兴趣爱好」主题体系。
5. **profile 读取语义**：`profile(chats=...)` 传当前对话时 LLM 相关性过滤（post_process/profile.py:30-95）；max_token_size 截断按 updated_at 倒序。`context()` 可一次拿 profile+event 组合包。
6. requirements 钉版本组（实测装通）：fastapi[standard] numpy openai opentelemetry-* pgvector psycopg2-binary python-dotenv pyyaml redis sqlalchemy structlog tiktoken typeguard volcengine-python-sdk[ark] + memobase(client)。

## 给 Phase 1/2 的输入

- Phase 1：把「pg0+redis+源码 uvicorn」固化成 `scripts/setup_memobase_server.sh`（仿 letta 脚本）；评测 config：`language: zh` + `enable_event_embedding: true` 指向本地 shim + `max_chat_blob_buffer_token_size: 8192`
- Phase 2 adapter：write=insert(ChatBlob, created_at=虚拟时间) 逐题批；flush=flush(sync=True)；read=`context(chats=[当前问题])` 或 profile+search_event 组合；clear=delete_user（每题独立 user）；溯源走 wrapped（时间标记反查）；写前查重防 event 重复
- 范式登记：**profile-centric 画像中心**（属性+事件双层异步提取），主场 = T5 长程画像一致性 + 兴趣演化类

## Phase 3 全量实测（2026-06-13，v01_full_memobase 149 题）

**成绩：avg_score 0.377（第 10）/ correct 26 / wrong 10 / evasive 113 / error 0**。
- 中下游解读：画像中心范式主场 = T5 长程画像一致性 + 兴趣演化，当前题库**主场缺席**
  （同 graphiti 时序图谱），基础召回题发挥不出画像优势。补长程题后复测。
- 工程实测：flush(sync=True) 同步画像抽取（~14s/批）稳定；中文原生零 patch；149 题零 error。
