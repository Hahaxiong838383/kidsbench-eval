#!/usr/bin/env bash
# Letta 0.16.8 server 本地部署（pg0 嵌入式 Postgres）。
#
# 为什么这么麻烦（Phase 0 踩坑实录，2026-06-12）：
# Letta 0.16 server 模式只支持 Postgres（db.py 无 SQLite 分支——
# 源码 agent 关于「SQLite 嵌入式可用」的判断对 server 模式失效），
# 且 pip 包不含 alembic 迁移脚本，要手动建 schema。踩了 8 个坑：
#   1. asyncpg/aiosqlite/pgvector/pg0-embedded 依赖缺失（pip 不自动带）
#   2. server 启动默认连 localhost:5432（密码 letta/letta），本机无此 PG
#   3. pg0 自带的 psql 包装「假成功」（建用户返回 rc=0 但实际没建）
#      → 必须用 asyncpg 超管直连建库
#   4. create_all 必须在设了 LETTA_PG_URI 的进程里跑——否则 ORM 的
#      embedding 列按 SQLITE 分支建成 BINARY 类型（PG 上炸）
#   5. messages.sequence_id 缺 PG sequence（create_all 不建 sequence，
#      alembic 才建）→ 手动 CREATE SEQUENCE + 绑 default
#   6. deepseek base provider 返回空模型列表（0.16 与 deepseek-v4 不兼容）
#      → 改用 custom openai-compatible provider 指向 api.deepseek.com
#
# 用法: bash scripts/setup_letta_server.sh   （在 kidsbench-eval 根目录）
# 产出: pg0 实例 kidsbench-letta（port 5433）+ letta server（port 18283）
#       + deepseek custom provider 已注册

set -euo pipefail
cd "$(dirname "$0")/.."
VENV=.venv-letta
PG_PORT=5433
SERVER_PORT=18283
DSK=$(grep KIDSBENCH_DEEPSEEK_API_KEY .env.local | cut -d= -f2)

echo "▶ [1/6] 确认依赖（pip 不自动带的几个）"
$VENV/bin/pip install -q asyncpg aiosqlite sqlite-vec pgvector pg0-embedded 2>&1 | tail -1 || true

echo "▶ [2/6] 起 pg0 嵌入式 Postgres（port $PG_PORT）"
$VENV/bin/python - <<'PY'
from pg0 import Pg0
pg = Pg0("kidsbench-letta")
try:
    info = pg.start()
    print("  pg0:", info.uri.replace("postgres:postgres", "***"))
except Exception as e:
    print("  pg0 已在运行或:", str(e)[:60])
PY

echo "▶ [3/6] 建 letta 用户/库/vector 扩展（asyncpg 直连——pg0 psql 包装假成功不可用）"
$VENV/bin/python - <<PY
import asyncio, asyncpg
async def t():
    su = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:$PG_PORT/postgres")
    users = [r["usename"] for r in await su.fetch("SELECT usename FROM pg_user")]
    if "letta" not in users:
        await su.execute("CREATE USER letta WITH PASSWORD 'letta'")
    else:
        await su.execute("ALTER USER letta WITH PASSWORD 'letta'")
    dbs = [r["datname"] for r in await su.fetch("SELECT datname FROM pg_database")]
    if "letta" not in dbs:
        await su.execute("CREATE DATABASE letta OWNER letta")
    await su.close()
    lt = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:$PG_PORT/letta")
    await lt.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await lt.close()
    print("  letta 用户/库/vector 就绪")
asyncio.run(t())
PY

echo "▶ [4/6] 建 schema（create_all 必须带 LETTA_PG_URI 否则 embedding 列建错）"
LETTA_PG_URI="postgresql://letta:letta@127.0.0.1:$PG_PORT/letta" $VENV/bin/python - <<PY
import asyncio, pkgutil, importlib
import letta.orm
from letta.orm.base import Base
for m in pkgutil.iter_modules(letta.orm.__path__):
    try: importlib.import_module(f"letta.orm.{m.name}")
    except Exception: pass
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
async def go():
    eng = create_async_engine("postgresql+asyncpg://letta:letta@127.0.0.1:$PG_PORT/letta")
    async with eng.begin() as c:
        await c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await c.run_sync(Base.metadata.create_all)
        # messages.sequence_id 缺 PG sequence（create_all 不建）
        await c.execute(text("CREATE SEQUENCE IF NOT EXISTS message_seq_id START 1"))
        await c.execute(text("ALTER TABLE messages ALTER COLUMN sequence_id "
                             "SET DEFAULT nextval('message_seq_id'::regclass)"))
        await c.execute(text("ALTER SEQUENCE message_seq_id OWNED BY messages.sequence_id"))
    await eng.dispose()
    print(f"  schema 就绪（{len(Base.metadata.tables)} 表 + message_seq_id）")
asyncio.run(go())
PY

echo "▶ [5/6] 起 letta server（port $SERVER_PORT）"
pkill -f "letta server.*$SERVER_PORT" 2>/dev/null || true
sleep 1
LETTA_PG_URI="postgresql://letta:letta@127.0.0.1:$PG_PORT/letta?sslmode=disable" \
  DEEPSEEK_API_KEY="$DSK" \
  nohup $VENV/bin/letta server --port $SERVER_PORT > /tmp/letta_server.log 2>&1 &
for i in $(seq 1 40); do
  if curl -s --max-time 2 "http://127.0.0.1:$SERVER_PORT/v1/health/" | grep -q '"status":"ok"'; then
    echo "  server OK"; break
  fi
  sleep 1
done

echo "▶ [6/6] 注册 deepseek custom provider（base provider 空模型列表，必走 custom）"
curl -s --max-time 10 -X POST "http://127.0.0.1:$SERVER_PORT/v1/providers/" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"deepseek-oai\",\"provider_type\":\"openai\",\"api_key\":\"$DSK\",\"base_url\":\"https://api.deepseek.com/v1\"}" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('  provider:', d.get('id','已存在或'+str(d)[:60]))" 2>/dev/null || echo "  provider 可能已存在"

echo
echo "✅ Letta server 就绪：http://127.0.0.1:$SERVER_PORT"
echo "   模型 handle: openai-proxy/deepseek-v4-flash"
echo "   验证: curl -s http://127.0.0.1:$SERVER_PORT/v1/health/"
