#!/bin/bash
# KidsBench 全量端到端跑批：caffeinate 防休眠 + 断点续跑 + NLI judge
#
# 用法（脱离 cc 后台超时，建议 nohup）：
#   nohup bash scripts/run_full.sh > /tmp/kb_full.log 2>&1 &
#   中断后重跑同命令 → --resume 自动跳过已完成题
#
# 环境变量：
#   QUESTIONS（默认 explore_v2_samples.jsonl）/ RUN_ID（默认 full）
#   PRESET（被测 LLM，默认 gemini-3-flash）/ JUDGE（NLI judge，默认 qwen-judge）
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
QUESTIONS="${QUESTIONS:-questions/explore_v2_samples.jsonl}"
RUN_ID="${RUN_ID:-full}"
PRESET="${PRESET:-gemini-3-flash}"
JUDGE="${JUDGE:-qwen-judge}"

cd "$ROOT"
echo "[run_full] questions=$QUESTIONS run_id=$RUN_ID preset=$PRESET judge=$JUDGE"
echo "[run_full] caffeinate + resume，中断可重跑续传"

# caffeinate -i 防空闲休眠；run_eval --resume 断点续跑（retry 内建网络重试）
caffeinate -i "$ROOT/.venv/bin/python" -m harness.run_eval \
  --questions "$QUESTIONS" \
  --out runs/full \
  --run-id "$RUN_ID" \
  --llm-preset "$PRESET" \
  --judge-preset "$JUDGE" \
  --resume

echo "[run_full] 完成 → runs/full/$RUN_ID/summary.json"
