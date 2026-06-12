# Letta 核实事实（Phase 0 产出）

> 日期：2026-06-12 ｜ 版本：letta 0.16.8 + letta-client 1.12.1（.venv-letta）
> 方法：本机实测（pg0 嵌入式 PG + deepseek custom provider 真跑 agent 中文对话）
> 部署脚本：`scripts/setup_letta_server.sh`（一键复现，含 8 坑修复）
> 背景：源码重盘点裁决（2026-06-12）Letta 排第二（工程最成熟 23K 星，
> 但中文要小改）；gpt+gemini 对抗审「范式重叠是评测必需」抬到与 ReMe 同档

## 红绿灯总表

| # | 核实点 | 结果 | 证据 |
|---|---|---|---|
| 1 | **LLM 注入【一票否决】** | 🟢（需 custom provider）| deepseek **base provider 返回空模型列表**（0.16 与 deepseek-v4 系列不兼容）→ 必走 custom openai-compatible provider 指向 api.deepseek.com，handle=`openai-proxy/deepseek-v4-flash`，实测 agent 中文对话通 |
| 2 | **中文可用【一票否决】** | 🟢 | 实测 agent 中文记忆+中文回复自然（「你养了一只布偶猫…喜欢吃冻干三文鱼」），且诚实区分未记录信息（无幻觉）。切句 regex（line_chunker.py:70 `[.!?]\s+[A-Z]`）只影响超长文档分块，对话级记忆不触发——Phase 0 范围内中文无障碍 |
| 3 | server 部署 | 🟡 重 | **只支持 Postgres**（db.py 无 SQLite 分支，源码 agent 判断 SQLite 可用对 server 失效）；pip 不带 alembic 迁移 → 手动 create_all + 补 message_seq_id sequence。pg0 嵌入式解决「无外部 PG」（与 hindsight 同款 pg0-embedded） |
| 4 | 范式 | 📌 | MemGPT 自管理记忆（agent 自主决定存/改 memory blocks + archival）。与 memoryos 分层构成范式内对照（对抗审「同范式交叉验证」原则） |

## 8 个部署坑（setup_letta_server.sh 已全修，避免重踩）

1. 依赖缺失：asyncpg / aiosqlite / sqlite-vec / **pgvector** / **pg0-embedded** pip 不自动带
2. server 默认连 localhost:5432（user letta/letta），本机无此 PG → 用 pg0 起 port 5433
3. **pg0 自带 psql 包装「假成功」**：建用户返回 rc=0 但 pg_user 里没有 → 必须 asyncpg 超管直连建库（no-guessing 教训：rc=0 ≠ 真成功，要查实际状态）
4. **create_all 必须带 LETTA_PG_URI 环境**：ORM 的 embedding 列类型在 import 时按 `settings.database_engine` 决定，裸跑（无 PG env）判定 SQLITE → 建成 BINARY 列 → PG 上炸
5. **messages.sequence_id 缺 PG sequence**：create_all 不建 sequence（alembic 的 e991d2e3b428 才建）→ 手动 `CREATE SEQUENCE message_seq_id` + `ALTER COLUMN SET DEFAULT nextval(...)` + `OWNED BY`
6. **deepseek base provider 空模型列表**：直接用 `deepseek/deepseek-chat` 报 `must be one of []` → custom openai provider PIVOT（关键转折）
7. server 启动 30s+（首次加载 + schema 校验），健康检查 `/v1/health/` 轮询
8. LETTA_ENCRYPTION_KEY 未设 → secret 明文存（评测无敏感数据，忽略；生产要设）

## 范式定位与 Phase 2 adapter 设计

- **write**：agent 对话喂 turn（保留 MemGPT 自管理范式精髓——agent 自主决定存什么）
  或直接 archival passage insert（更可控，溯源走 passage metadata）
- **read**：archival search + memory blocks 直读，拿原始记忆给**统一回答模型**
  （不让 Letta 自己的 agent 回答——那会污染评测协议的回答端一致性）
- **溯源**：passage 有 `id` + `metadata`（Optional[Dict]，可存 turn_id），source agent 实证
- **隔离**：每题一个 agent（organization_id 隔离）或共享 agent + 每题清 archival
- **范式登记**：MemGPT 自管理记忆，与 memoryos 分层做对照

## 给 Phase 1/2 的输入

- Phase 1：把 setup_letta_server.sh 纳入评测前置（harness 起 server 前调）；
  评测用 archival 直插路径（绕开 agent 自管理的不确定性，溯源更稳）
- Phase 2 adapter：LettaAdapter 用 letta_client SDK；write=archival passage insert
  带 turn_id metadata；read=archival search 拿 passage（溯源 native）；
  clear=删 agent/清 archival；考虑用统一回答模型而非 Letta agent 回答
- 范式覆盖地图更新：Letta 主场 = T1/T2（自管理记忆的跨会话保持）
