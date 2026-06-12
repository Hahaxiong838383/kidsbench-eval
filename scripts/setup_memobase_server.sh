#!/usr/bin/env bash
# Memobase server 本地部署（pg0 嵌入式 PG + redis + 源码 uvicorn，Phase 0 实测 2026-06-12）。
#
# 为什么这么起（Phase 0 踩坑）：
# - server 无法 pip install（pyproject flat-layout 多包冲突），必须源码目录直跑 uvicorn
# - 官方只提供 docker-compose；本机用 pg0 嵌入式 PG（无外部 PG）+ brew redis 替代
# - ⚠️ server 启动会把 llm_api_key 全文打进日志（本机评测无所谓，生产要管日志级别）
#
# 依赖：本地 embedding shim 在 18230（enable_event_embedding=true 时 event 检索必需）。
#   .venv/bin/python -m kidsbench.middleware.embedding_shim --port 18230
#
# 用法: bash scripts/setup_memobase_server.sh   （在 kidsbench-eval 根目录）
# 产出: Memobase server（http://127.0.0.1:8019，ACCESS_TOKEN=kb-phase0-secret）
#       pg0 实例 kidsbench-memobase（5434）+ redis（6399）

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
VENV="$ROOT/.venv-memobase"
MEMOBASE_SRC=/tmp/kb-survey/memobase/src/server/api
PG_PORT=5434
REDIS_PORT=6399
PORT=8019
TOKEN=kb-phase0-secret

if [ ! -x "$VENV/bin/uvicorn" ]; then
  echo "❌ $VENV 不存在。先建 venv 装依赖（见 docs/MEMOBASE_VERIFIED_FACTS.md 工程事实 #6）"
  exit 1
fi
if [ ! -d "$MEMOBASE_SRC" ]; then
  echo "❌ $MEMOBASE_SRC 不存在。先 git clone https://github.com/memodb-io/memobase /tmp/kb-survey/memobase"
  exit 1
fi

# 已在跑就跳过
if curl -s -m 3 "http://127.0.0.1:$PORT/api/v1/healthcheck" -H "Authorization: Bearer $TOKEN" 2>/dev/null | grep -q '"errno":0'; then
  echo "✅ Memobase 已在 $PORT 运行，跳过"
  exit 0
fi

echo "▶ [1/5] 起 pg0 嵌入式 PG（$PG_PORT）+ 建库 + vector 扩展"
"$VENV/bin/python" - <<PY
from pg0 import Pg0
try:
    info = Pg0("kidsbench-memobase", port=$PG_PORT).start()
    print("  pg0 started")
except Exception as e:
    print("  pg0 已在运行或:", str(e)[:60])
PY
"$VENV/bin/python" - <<PY
import asyncio, asyncpg
async def main():
    su = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:$PG_PORT/postgres")
    try:
        await su.execute("CREATE DATABASE memobase")
    except asyncpg.DuplicateDatabaseError:
        pass
    await su.close()
    db = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:$PG_PORT/memobase")
    await db.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await db.close()
    print("  memobase db + vector ext OK")
asyncio.run(main())
PY

echo "▶ [2/5] 起 redis（$REDIS_PORT）"
if /opt/homebrew/bin/redis-cli -p $REDIS_PORT ping 2>/dev/null | grep -q PONG; then
  echo "  redis 已在运行"
else
  /opt/homebrew/bin/redis-server --port $REDIS_PORT --daemonize yes --save '' >/dev/null
  echo "  redis started"
fi

echo "▶ [3/5] 检查 embedding shim（18230）"
if ! curl -s -m 3 http://127.0.0.1:18230/v1/embeddings \
     -H "Content-Type: application/json" \
     -d '{"model":"BAAI/bge-small-zh-v1.5","input":["x"]}' >/dev/null 2>&1; then
  echo "⚠️  shim 18230 未响应。先跑：.venv/bin/python -m kidsbench.middleware.embedding_shim --port 18230"
  exit 1
fi

echo "▶ [4/5] 写 config.yaml（key 脱敏，chmod 600）"
DSK=$(grep '^KIDSBENCH_DEEPSEEK_API_KEY=' "$ROOT/.env.local" | cut -d= -f2)
cat > "$MEMOBASE_SRC/config.yaml" <<EOF
llm_api_key: $DSK
llm_base_url: https://api.deepseek.com/v1
best_llm_model: deepseek-v4-flash
thinking_llm_model: deepseek-v4-flash
language: zh
enable_event_embedding: true
embedding_provider: openai
embedding_api_key: dummy-local
embedding_base_url: http://127.0.0.1:18230/v1
embedding_model: BAAI/bge-small-zh-v1.5
embedding_dim: 512
max_chat_blob_buffer_token_size: 8192
EOF
chmod 600 "$MEMOBASE_SRC/config.yaml"

echo "▶ [5/5] 起 server（后台，启动含 llm_sanity_check 真调，~30s）"
cd "$MEMOBASE_SRC"
env DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:$PG_PORT/memobase" \
    REDIS_URL="redis://localhost:$REDIS_PORT/0" \
    ACCESS_TOKEN="$TOKEN" PROJECT_ID="kidsbench" \
    nohup "$VENV/bin/uvicorn" api:app --port $PORT > /tmp/memobase_server.log 2>&1 &
for i in $(seq 1 14); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/api/v1/healthcheck" -H "Authorization: Bearer $TOKEN" 2>/dev/null | grep -q '"errno":0'; then
    echo "✅ Memobase 就绪：http://127.0.0.1:$PORT（ACCESS_TOKEN=$TOKEN）"
    exit 0
  fi
  sleep 3
done
echo "❌ server 启动超时，看 /tmp/memobase_server.log"
exit 1
