# KidsBench Web Backend — B0

> 阶段：B0（架构白盒展示）
> SPEC: [`../../docs/WEB_PLATFORM_PHASE_B0.md`](../../docs/WEB_PLATFORM_PHASE_B0.md)

## 本地启动

```bash
# 创建独立 venv（不污染 .venv-mem0/.venv-memoryos/.venv-graphiti）
cd ~/mycc/kidsbench-eval
python3 -m venv .venv-web
.venv-web/bin/pip install -r web/backend/requirements.txt

# 启动 dev server
cd web/backend
../../.venv-web/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 验证

```bash
# 健康检查
curl http://127.0.0.1:8000/healthz

# 架构索引
curl http://127.0.0.1:8000/api/architecture | jq

# graphiti 实时状态（需先开 QNAP FalkorDB SSH tunnel）
ssh -L 16379:127.0.0.1:16379 qnap-via-mini -N &
curl http://127.0.0.1:8000/api/state/graphiti | jq

# mem0 / memoryos 状态（从最近 final.json 读）
curl http://127.0.0.1:8000/api/state/mem0 | jq
curl http://127.0.0.1:8000/api/state/memoryos | jq

# 历史 run 列表
curl 'http://127.0.0.1:8000/api/runs?limit=20' | jq
curl 'http://127.0.0.1:8000/api/runs?adapter=mem0&era=after_bge' | jq
```

## 环境变量

| Key | 默认 | 说明 |
|---|---|---|
| `KIDSBENCH_RUNS_PATH` | `<project>/runs` | 历史 run 目录 |
| `KIDSBENCH_QUESTIONS_PATH` | `<project>/questions` | 题库目录 |
| `FALKOR_HOST` | `127.0.0.1` | FalkorDB 主机 |
| `FALKOR_PORT` | `16379` | FalkorDB 端口（QNAP 默认 16379） |
| `CORS_ORIGINS` | `http://localhost:5173,...` | 前端 CORS 白名单 |

## 测试

```bash
cd web/backend
../../.venv-web/bin/pytest -xvs tests/
```

## API endpoint 全集（B0）

| Method | Path | 用途 |
|---|---|---|
| GET | `/` | endpoint 索引 |
| GET | `/healthz` | 健康检查 |
| GET | `/docs` | Swagger UI |
| GET | `/api/architecture` | 完整架构索引 |
| GET | `/api/architecture/adapter/{name}` | 单 adapter 元信息 |
| GET | `/api/architecture/memory/{name}` | 单记忆系统元信息 |
| GET | `/api/state/mem0` | mem0 状态（非实时）|
| GET | `/api/state/memoryos` | memoryos 状态（非实时）|
| GET | `/api/state/graphiti` | graphiti 状态（实时）|
| GET | `/api/runs?adapter=&era=&limit=` | 历史 run 列表 |
| GET | `/api/runs/{group}/{run_id}` | 单 run 详情 |
