# KidsBench Web Platform — V3 完整 SPEC

> 版本：V3（题库管理 + 白盒观测 一体化）
> 日期：2026-05-29
> 作者：cc + 川哥拍板 + gemini 对抗审（13 finding 已合并）
> 状态：已拍方向，B0 待开工

---

## 1. 目标 / 非目标

### 目标

把 KidsBench 评测项目从"本地 CLI + 散文件"升级为"全流程 Web 平台"，闭环 4 个角色：

1. **策划者**：在 web 写 / 改 / 标签题库
2. **执行者**：一键触发跑题（3 adapter × 3 memory system 横向）
3. **观察者**：实时看 pipeline 时间线 + 中间产物（embedding / LLM call / 召回 / 评分）
4. **复盘者**：横向 diff 召回结果 / 历史 run 回放 / 题库版本对比

核心价值：把"改题 → 跑 → 看结果"的摩擦从 5 分钟降到 30 秒，让题库迭代速度 5-10x。

### 非目标（明确不做）

- ❌ 不做训练 / 微调 / 模型切换 UI（仍走 .env）
- ❌ 不做用户权限分级（B1 阶段单用户：川哥）
- ❌ 不做 LLM-as-judge 第二判分（等 LLM 模型锁定后再做，已记 task #37）
- ❌ 不做生产监控（dashboard / 告警 / SLO）—— 这是评测平台不是生产服务

---

## 2. 整体架构

### 2.1 物理拓扑

```
川哥手机 / PC 浏览器
    ↓ HTTPS
kidsbench.cli4.hahaxiong.cc (DNS → 阿里云 HK 8.218.26.17)
    ↓ HK nginx (复用 multica 链路，加 server block)
    ↓ proxy_pass 127.0.0.1:18081 (前端) / :18444 (API)
    ↓ FRP server → Mini frpc (复用 multica frpc，加 2 个 proxy)
    ↓ Mini → QNAP 内网 (192.168.61.18)
    ↓
QNAP TS-X65 (Container Station)
    ├─ kidsbench-frontend:18080 → nginx + React 静态站
    ├─ kidsbench-backend:18000  → FastAPI + SQLite
    ├─ kidsbench-runs/          → /share/Container/kidsbench-runs/ (rsync 同步进来)
    └─ FalkorDB:16379           → (已有，graphiti 用)
    ↑
Air (开发机 / 跑题机)
    ├─ harness (caffeinate -i 防睡眠)
    ├─ .venv-mem0 / .venv-memoryos / .venv-graphiti
    ├─ Air webhook receiver :9000 (B3 阶段加)
    └─ rsync watchdog (pm2 守护，同步 runs/ 到 QNAP)
```

### 2.2 数据流（实时）

```
Air harness 跑题
    │
    ├─[1] 每个 hook 点 → HTTP POST → QNAP /api/run/{id}/event   ◀━ 实时热数据
    │                                       ↓
    │                                  asyncio.Queue
    │                                       ↓
    │                                  SSE 推送给前端
    │
    └─[2] 写本地 /tmp/runs/<run_id>/ → 跑完 mv 到 ~/runs/         ◀━ 原子替换
              ↓
          fswatch + rsync → QNAP /share/Container/kidsbench-runs/  ◀━ 冷数据归档
```

**关键设计原则**（Gemini A.1 合并）：rsync 只做冷数据备份，**实时事件必须 HTTP 直推**，否则 SSE 延迟 1-5s 体验崩。

---

## 3. 阶段拆分（4 期）

| 阶段 | 目标 | 工时 | 上线条件 |
|---|---|---|---|
| **B0** | 架构白盒展示（看现状）| 1.5d | 跑过本地，QNAP 部署 |
| **B1** | Pipeline 时间线 + 历史 run 回放 | 2d | B0 + 实时事件流通 |
| **B2** | 题库 CRUD（SQLite + 编辑器）| 2d | B1 + DB 接入 harness |
| **B3** | 触发跑题（webhook + B3 控制台）| 1.5d | B2 + Air webhook 通 |
| **合计** | | **7d** | |

每阶段交付物详见各阶段独立 plan：
- B0 → `docs/WEB_PLATFORM_PHASE_B0.md`
- B1 → 待 B0 完成后写
- B2 → 待 B1 完成后写
- B3 → 待 B2 完成后写

---

## 4. 数据模型

### 4.1 题库 SQLite Schema（B2 落地）

```sql
-- 题目表（含 version 自增 + 软删除）
CREATE TABLE questions (
    qid TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload JSON NOT NULL,           -- 题目完整内容（write_turns / query / expected）
    category TEXT,                   -- unguessable / counterfactual / distractor / paraphrase
    difficulty TEXT,                 -- easy / medium / hard
    age_band TEXT,                   -- K1-K3 / K4-K6 / K7-K9 / K10-K12
    tags JSON,                       -- ["宠物", "家庭"]
    status TEXT DEFAULT 'active',    -- active / archived / draft
    author TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (qid, version)
);

CREATE INDEX idx_questions_status ON questions(status);
CREATE INDEX idx_questions_category ON questions(category);

-- 题目 payload 示例：
-- {
--   "write_turns": [
--     {"role": "user", "text": "我家有只布偶猫叫团子"},
--     {"role": "assistant", "text": "团子真可爱"}
--   ],
--   "query": "团子是什么品种",
--   "expected_keywords": ["布偶猫", "布偶"],
--   "expected_answer_semantic": "猫的品种是布偶猫",
--   "grader_config": {"type": "hybrid", "weights": {"keyword": 0.6, "semantic": 0.4}}
-- }

-- 编辑历史（audit log）
CREATE TABLE question_edits (
    edit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    qid TEXT NOT NULL,
    from_version INTEGER,
    to_version INTEGER NOT NULL,
    diff JSON,                       -- 字段级 diff
    author TEXT,
    edited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4.2 Run 数据格式（B1 落地）

每次 run 落地到 `runs/<run_id>/`：

```
runs/<run_id>/
├── meta.json              # 题目自包含快照（含 question_version）
├── pipeline.jsonl         # span 模型事件流
└── final.json             # 答案 + 评分
```

**meta.json**（题库一致性，Gemini D.4 合并）：

```json
{
  "run_id": "r_20260529_103045_abc",
  "qid": "q_001",
  "question_version": 3,
  "question_snapshot": { ... 完整题目内容 ... },
  "adapter": "mem0",
  "config_hash": "sha256...",
  "started_at": "2026-05-29T10:30:45Z",
  "completed_at": "2026-05-29T10:31:02Z",
  "embedding_model": "BAAI/bge-small-zh-v1.5",
  "llm_model": "gemini-3.5-flash"
}
```

**pipeline.jsonl**（OTel-lite span 模型，Gemini C.1 合并）：

```json
{"span_id":"sp-1","parent_id":null,"name":"run_question","type":"ENTER","ts":1234567890.000}
{"span_id":"sp-2","parent_id":"sp-1","name":"adapter.write","adapter":"mem0","type":"ENTER","ts":1234567890.100,"turn_text":"我家有只布偶猫叫团子"}
{"span_id":"sp-3","parent_id":"sp-2","name":"embedding.encode","is_query":false,"text_preview":"我家有只...","embedding_b64":"AaBb...","dim":512,"type":"EXIT","ts":1234567890.150,"duration_ms":50}
{"span_id":"sp-4","parent_id":"sp-2","name":"llm.call","provider":"gemini-3.5-flash","prompt_tokens":120,"completion_tokens":45,"type":"EXIT","ts":1234567890.250,"duration_ms":100}
{"span_id":"sp-2","parent_id":"sp-1","name":"adapter.write","type":"EXIT","ts":1234567890.300,"duration_ms":200}
...
```

**Embedding 内嵌**（Gemini C.2 合并）：不再单独存 `embeddings/*.npy`，向量直接 base64 编码塞进 span event。

**final.json**：

```json
{
  "run_id": "r_...",
  "answer": "团子是布偶猫",
  "grader": {
    "keyword_match": true,
    "keyword_matched": ["布偶猫"],
    "semantic_score": 0.87,
    "weighted_score": 1.0,
    "passed": true
  },
  "stats": {
    "total_duration_ms": 17234,
    "embedding_calls": 12,
    "llm_calls": 3,
    "tokens": {"prompt": 8421, "completion": 234}
  }
}
```

---

## 5. API 设计

### 5.1 题库 CRUD（B2）

| Method | Path | 功能 |
|---|---|---|
| GET | `/api/questions` | 列表 + 筛选（query: category / tag / status）|
| GET | `/api/questions/{qid}` | 详情（最新 version）|
| POST | `/api/questions` | 创建新题 |
| PATCH | `/api/questions/{qid}` | 编辑（自动 +version）|
| DELETE | `/api/questions/{qid}` | 软删除（status=archived）|
| GET | `/api/questions/{qid}/versions` | 历史版本列表 |
| POST | `/api/questions/{qid}/restore?to_version=N` | 回滚 |

### 5.2 Run 管理（B1 + B3）

| Method | Path | 功能 |
|---|---|---|
| GET | `/api/runs` | 历史列表 + 筛选 |
| GET | `/api/runs/{run_id}` | 详情（meta + final）|
| GET | `/api/runs/{run_id}/pipeline` | 完整 span 树（用于回放）|
| GET | `/api/runs/{run_id}/stream?last_event_id=N` | SSE 实时流（Gemini B.2 Last-Event-ID）|
| POST | `/api/run` `{qid, adapters[]}` | 触发新 run（B3 → 转 Air webhook）|
| GET | `/api/diff?run_ids=r1,r2,r3` | 横向 diff 召回结果 |

### 5.3 状态快照（B0）

| Method | Path | 功能 |
|---|---|---|
| GET | `/api/state/mem0?user_id=X` | Qdrant collection 点数 / facts |
| GET | `/api/state/memoryos?user_id=X` | 三层占用快照 |
| GET | `/api/state/graphiti?user_id=X` | 图谱节点 / 边 / latest episode |
| GET | `/api/architecture` | 架构索引（B0 静态数据）|

### 5.4 Event Ingestion（Air → QNAP）

| Method | Path | 功能 |
|---|---|---|
| POST | `/api/run/{run_id}/event` | Air harness 推送 span event |
| POST | `/api/run/{run_id}/complete` | Air harness 通知 run 结束 |

---

## 6. 部署（QNAP + HK 链路）

### 6.1 QNAP Container Station

```yaml
# /share/Container/kidsbench/docker-compose.yml
services:
  backend:
    image: kidsbench-backend:latest
    ports: ["18000:8000"]
    volumes:
      - /share/Container/kidsbench/data:/app/data   # SQLite + questions.db
      - /share/Container/kidsbench-runs:/app/runs:ro
    environment:
      - DB_PATH=/app/data/questions.db
      - RUNS_PATH=/app/runs
    restart: always
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 30s

  frontend:
    image: nginx:alpine
    ports: ["18080:80"]
    volumes:
      - ./dist:/usr/share/nginx/html:ro
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    restart: always
```

**端口选择理由**（Gemini D.1 合并）：QNAP 系统占用 80/443/8000/8080，统一用 18xxx 段避免冲突。

### 6.2 HK nginx server block

```nginx
# /etc/nginx/sites-available/kidsbench (HK VPS 8.218.26.17)
server {
    listen 443 ssl http2;
    server_name kidsbench.cli4.hahaxiong.cc;

    ssl_certificate /etc/letsencrypt/live/kidsbench.cli4.hahaxiong.cc/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kidsbench.cli4.hahaxiong.cc/privkey.pem;

    # SSE 长连接优化（Gemini B.1 合并）
    location ~ ^/api/run/.*?/stream$ {
        proxy_pass http://127.0.0.1:18444;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        chunked_transfer_encoding on;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        add_header X-Accel-Buffering no;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:18444;
        proxy_set_header Host $host;
    }

    location / {
        proxy_pass http://127.0.0.1:18081;
    }

    # Basic Auth（MVP，量上来再换 CF Zero Trust）
    auth_basic "kidsbench";
    auth_basic_user_file /etc/nginx/.htpasswd-kidsbench;
}
```

### 6.3 Mini frpc 新增 proxy

```toml
# ~/mycc/2-Projects/cc-remote-v4/frpc.toml 追加
[[proxies]]
name = "kidsbench-web"
type = "tcp"
localIP = "192.168.61.18"   # QNAP 内网
localPort = 18080
remotePort = 18081

[[proxies]]
name = "kidsbench-api"
type = "tcp"
localIP = "192.168.61.18"
localPort = 18000
remotePort = 18444
```

---

## 7. 安全

### 7.1 公网层

- **HTTPS**：Let's Encrypt 申请 `kidsbench.cli4.hahaxiong.cc` 子证书（与 multica 同级别）
- **Basic Auth**：MVP 阶段 nginx 层（用户名密码塞 `.htpasswd-kidsbench`）
- **HTTP → HTTPS 强制跳转**：HK nginx 加 80 端口 301 重定向

### 7.2 数据层

- **题库数据**：SQLite 文件 chmod 600，每日凌晨 cron 备份到 QNAP 另一目录（与 multica 一样）
- **Run 数据**：包含儿童对话 + 评测过程，不嵌入到题库，独立目录权限
- **LLM API Key**：仅 Air 上 `.env`，QNAP 后端不持有

### 7.3 凭据

- HK VPS 证书：复用 cli4.hahaxiong.cc wildcard 或新签 kidsbench 子域
- Basic Auth 凭据：单用户（川哥），存 Air 1Password / Keychain

---

## 8. Gemini 13 Finding 落地清单

| # | Finding | 决策 | 落地位置 |
|---|---|---|---|
| A.1 | rsync vs 实时 | ✅ 采纳：HTTP POST 双通道 | §2.2 数据流图 |
| A.2 | QNAP→Air 触发 | ⚠️ 简化：B3 阶段加 webhook | §3 阶段拆分 |
| A.3 | 文件原子性 | ✅ 采纳：临时目录 + mv | §2.2 + harness 改动 |
| B.1 | nginx idle timeout | ✅ 采纳：proxy_buffering off + 3600s | §6.2 nginx config |
| B.2 | Last-Event-ID | ✅ 采纳：SSE 加 id + 重连 query param | §5.2 API + 后端实现 |
| B.3 | Cloudflare 限制 | ⏭ N/A：不经 CF | — |
| B.4 | FRP vs WireGuard | ⏸ MVP 用 FRP（multica 已稳），监控丢包 | §6.3 |
| C.1 | span_id/parent_id | ✅ 采纳：OTel-lite span 模型 | §4.2 pipeline.jsonl |
| C.2 | embedding 小文件爆炸 | ✅ 采纳：base64 内嵌 jsonl | §4.2 |
| C.3 | reactflow 卡顿 | ✅ 采纳：memo + throttle 5Hz | B1 前端实现 |
| C.4 | 横向 diff 对齐 | ⏸ B2 阶段做 | — |
| D.1 | QNAP container 启动顺序 | ✅ 采纳：depends_on + healthcheck | §6.1 |
| D.2 | rsync 守护脆弱 | ✅ 采纳：caffeinate + pm2 | harness 启动包装 |
| D.3 | Basic Auth 裸奔 | ⚠️ MVP 用 Basic Auth，量上来换 CF Zero Trust | §7.1 |
| D.4 | 题库版本一致性 | ✅ 采纳：meta.json 自包含 question_snapshot | §4.2 |
| E.1 | Mac 休眠 | ✅ 采纳：caffeinate -i 包 harness | harness 启动 |
| F.1 | SQLite 单文件替代散文件 | ⏸ V4 演进路径，B1 先用 jsonl | — |
| F.2 | Jaeger 替代自研 | ❌ 不采纳：Jaeger 是 trace 工具，川哥要 dashboard | — |
| F.3 | 架构反转 Air 主导 | ❌ 不采纳：丢失 NAS 24h 在线优势 | — |

完整 gemini 评审存档：`/tmp/gemini_kidsbench_web_review.md`（12K 字）

---

## 9. 技术栈

### 9.1 后端

- **Python 3.11** + **FastAPI** + **uvicorn**
- **SQLite**（题库）/ 读 jsonl（run 数据）
- **sse-starlette**（SSE 推送）
- **pydantic v2**（schema 验证）

### 9.2 前端

- **React 18** + **Vite** + **TypeScript**
- **Tailwind CSS** + **shadcn/ui**（组件库）
- **TanStack Query**（数据获取）
- **reactflow**（B1+ pipeline 时间线）
- **recharts**（B1+ 评分趋势图）

**为什么不用 streamlit / next.js**：streamlit 表达 swimlane 太勉强，next.js 全栈杀鸡用牛刀。React + Vite + FastAPI 分体最直接。

### 9.3 部署

- QNAP Container Station + docker-compose
- HK VPS nginx + Let's Encrypt
- Mini frpc（复用 multica 链路）

---

## 10. 现有资产代码索引（B0 重点展示）

> 按 source-analysis.md 规则：B0 阶段把这些代码位置在 web 上以可点击索引形式展示

### 10.1 契约层

- [`src/kidsbench/contract/__init__.py`](../src/kidsbench/contract/__init__.py) — `MemoryAdapter` ABC + 7 方法
- [`src/kidsbench/contract/types.py`](../src/kidsbench/contract/types.py) — `Turn` / `RecallResult` / `CapabilityProfile`

### 10.2 Adapter 层（3 个）

- [`src/kidsbench/adapters/mem0_adapter.py`](../src/kidsbench/adapters/mem0_adapter.py) — `Mem0Adapter`，接 `mem0ai` 2.0.4
- [`src/kidsbench/adapters/memoryos_adapter.py`](../src/kidsbench/adapters/memoryos_adapter.py) — `MemoryosAdapter`，接 `Memoryos` 类（GitHub install）
- [`src/kidsbench/adapters/graphiti_adapter.py`](../src/kidsbench/adapters/graphiti_adapter.py) — `GraphitiAdapter`，接 `graphiti-core` 0.18.9

### 10.3 中间件（L0.5）

- [`src/kidsbench/middleware/graphiti_compat.py`](../src/kidsbench/middleware/graphiti_compat.py) — `_RealGraphitiWrapper`（持久 event loop） + `make_st_embedder` + `make_real_graphiti_client_factory`
- [`src/kidsbench/middleware/embedding.py`](../src/kidsbench/middleware/embedding.py) — `EmbeddingService` 统一接口

### 10.4 Harness

- [`harness/run_eval.py`](../harness/run_eval.py) — 主入口，含 `make_mem0_adapter` / `make_memoryos_adapter` / `make_graphiti_adapter`

### 10.5 题库

- [`questions/smoke.jsonl`](../questions/smoke.jsonl) — 6 题 smoke 集（B0 阶段从这里读）

### 10.6 Run 历史

- `runs/mem0_bge/` / `runs/memoryos_bge/` / `runs/graphiti_bge/` — bge 切换后的实测

---

## 11. 后续 SPEC 引用

- B0 阶段：`docs/WEB_PLATFORM_PHASE_B0.md`
- B1 阶段：待写
- B2 阶段：待写
- B3 阶段：待写

---

**审阅人**：川哥
**实施人**：cc（主线） + codex（5/31 限额恢复后协同前端实现）+ gemini（每阶段对抗审）
**总工时**：~7d（B0 1.5d + B1 2d + B2 2d + B3 1.5d）
