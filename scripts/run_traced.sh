#!/bin/bash
# 带实时事件推送的跑题包装器（2026-06-14）。
# 确保 dev198→QNAP 后端隧道(localhost:18001→192.168.61.18:18001)在，跑 run_eval 带 B1 trace
# → 每个 pipeline 事件实时 POST 到 web 后端事件总线 → 网页「观察者」实时看跑题过程。
# 事件端点 app 层无鉴权，走隧道直连 QNAP 后端绕过 HK 边缘 Basic Auth，零凭证。
#
# 用法: bash run_traced.sh <venv目录> <run_eval 参数...>
#   例: bash run_traced.sh .venv-mem0 --include-mem0 --skip-baselines \
#         --questions questions/v01_smoke.jsonl --judge-preset qwen-judge \
#         --llm-preset gemini-3-flash --out runs/dev198_v01 --run-id traced_mem0 --resume
set -uo pipefail
cd "$HOME/kidsbench-eval" || exit 9

TUNNEL_PORT=18001
ensure_tunnel() {
  curl -s -m5 "http://localhost:$TUNNEL_PORT/api/runs/sources" >/dev/null 2>&1 && return 0
  echo "[trace] 隧道 $TUNNEL_PORT 不在，重建 dev198→QNAP 后端..." >&2
  ssh -fN -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=10 -L $TUNNEL_PORT:192.168.61.18:18001 -p 22022 rayman.chen@cli4.hahaxiong.cc 2>/dev/null
  sleep 3
  curl -s -m5 "http://localhost:$TUNNEL_PORT/api/runs/sources" >/dev/null 2>&1
}

if ensure_tunnel; then
  echo "[trace] ✅ 隧道OK，实时事件推送已启用（网页观察者可实时看）" >&2
  TRACE_ARGS=(--trace --trace-http "http://localhost:$TUNNEL_PORT/api/run/{run_id}/event")
else
  echo "[trace] ⚠️ 隧道未通，事件推送失效——评测照常跑，只是没实时观测" >&2
  TRACE_ARGS=()
fi

VENV="$1"; shift
export HF_ENDPOINT=https://hf-mirror.com
exec "$VENV/bin/python" -m harness.run_eval "$@" "${TRACE_ARGS[@]}"
