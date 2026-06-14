#!/bin/bash
# dev198 迁移全系统对账：dev198 runs/dev198_v01/v01_full_<sys> vs Air runs/v01_full_<sys>
# 逐题比 recall_metric(确定性,迁移保真铁证) + judge_verdict 一致率。验收=recall 100%一致。
# 用法: bash scripts/reconcile_dev198.sh   (在 Air kidsbench-eval 根目录)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
REC="$ROOT/scripts/reconcile.py"
for sys in memmachine cognee hindsight mem0 memoryos reme graphiti memobase letta; do
  AIR=$(find "runs/v01_full_$sys" -name results.jsonl 2>/dev/null | head -1)
  [ -z "$AIR" ] && { echo "[$sys] 无 Air 基线, 跳过"; continue; }
  scp -q "dev198:~/kidsbench-eval/runs/dev198_v01/v01_full_$sys/results.jsonl" "/tmp/dev198_${sys}.jsonl" 2>/dev/null \
    || { echo "[$sys] dev198 结果未就绪, 跳过"; continue; }
  python3 "$REC" "$AIR" "/tmp/dev198_${sys}.jsonl" "$sys"
done
