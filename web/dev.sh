#!/usr/bin/env bash
# B0 一键启动 dev server（后端 + 前端）
# 用法：./web/dev.sh
# 停止：pkill -f "uvicorn app.main" && pkill -f "vite"

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# ─── venv / npm 兜底
if [ ! -d ".venv-web" ]; then
  echo "[setup] 创建 .venv-web (Python 3.12)"
  /Users/rayman.chen/.local/bin/python3.12 -m venv .venv-web
  .venv-web/bin/pip install --quiet -r web/backend/requirements.txt
fi

if [ ! -d "web/frontend/node_modules" ]; then
  echo "[setup] npm install 前端依赖"
  (cd web/frontend && npm install)
fi

# ─── FalkorDB SSH tunnel 检查（B0：graphiti 实时状态需要）
echo
if nc -z 127.0.0.1 16379 2>/dev/null; then
  echo "[tunnel] ✅ FalkorDB :16379 已通（graphiti 状态可实时拉）"
else
  echo "[tunnel] ⚠  FalkorDB :16379 未通（graphiti 状态会降级展示历史快照）"
  echo "         开启实时拉取（需 prnas 密码）："
  echo "         ssh -L 16379:127.0.0.1:16379 prnas -N &"
fi
echo

# ─── backend
echo "[backend] 启动 uvicorn 127.0.0.1:8000 (--reload)"
(cd web/backend && PYTHONPATH=. ../../.venv-web/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload > /tmp/kidsbench_backend.log 2>&1) &
BACK_PID=$!
echo "  backend PID=$BACK_PID  log=/tmp/kidsbench_backend.log"

# ─── frontend
echo "[frontend] 启动 vite dev 0.0.0.0:5173"
(cd web/frontend && npm run dev > /tmp/kidsbench_frontend.log 2>&1) &
FRONT_PID=$!
echo "  frontend PID=$FRONT_PID  log=/tmp/kidsbench_frontend.log"

sleep 3
echo
echo "✅ 启动完毕"
echo "   后端：http://127.0.0.1:8000  (docs: /docs)"
echo "   前端：http://127.0.0.1:5173"
echo
echo "停止：pkill -f 'uvicorn app.main' && pkill -f 'vite'"
