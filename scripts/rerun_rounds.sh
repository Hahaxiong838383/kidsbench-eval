#!/bin/bash
# KidsBench 评测「多轮重跑 + 聚合统计」驱动脚本
#
# 职责：
#   1. 用 caffeinate -i 包装，防止 Mac 空闲/合盖导致的休眠中断长任务
#   2. 按环境变量/默认值执行 N 轮 × 多 adapter 重跑
#   3. 每轮每家失败只告警、不中断整体流程（graphiti 失败常见，因需外部 tunnel）
#   4. 全部完成后自动调用 aggregate_runs.py 做均值+标准差+稳定性聚合
#
# 用法示例：
#   # 默认 5 轮 + 3 家 + gemini-3-flash + smoke 题
#   ./scripts/rerun_rounds.sh
#
#   # 自定义：只跑 3 轮 mem0+memoryos
#   ROUNDS=3 ADAPTERS="mem0 memoryos" PRESET=gemini-3.5-flash ./scripts/rerun_rounds.sh
#
#   # 跑真实题库（注意题量大、耗时长）
#   QUESTIONS=questions/real.jsonl ROUNDS=5 ./scripts/rerun_rounds.sh
#
# 关键设计：
#   - 时间戳严格用 date -u +%Y%m%d_%H%M%S（无其他随机源）
#   - 每轮每家输出落到 runs/rerun_<ts>/<adapter>_r<i>/  （--out + --run-id 组合避免嵌套）
#   - set -uo pipefail（故意不 set -e，容忍单家失败继续）
#   - 聚合永远用项目 .venv（若不存在回退 python3）

set -uo pipefail

# ========== 参数（环境变量优先，无则默认值） ==========
ROUNDS=${ROUNDS:-5}
ADAPTERS_STR=${ADAPTERS:-"mem0 memoryos graphiti"}
PRESET=${PRESET:-"gemini-3-flash"}
QUESTIONS=${QUESTIONS:-"questions/smoke.jsonl"}

# ROUNDS 强类型校验：必须非负整数（防 ROUNDS=abc / 空值导致算术循环崩溃）
if [[ ! "$ROUNDS" =~ ^[0-9]+$ ]]; then
  echo "❌ 错误: ROUNDS 必须是非负整数，当前为 '$ROUNDS'" >&2
  exit 1
fi

# ========== 定位项目根 ==========
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ========== 时间戳 + 本批根目录 ==========
TS=$(date -u +%Y%m%d_%H%M%S)
BASE_DIR="runs/rerun_${TS}"
mkdir -p "$ROOT/$BASE_DIR"

echo "=================================================="
echo "KidsBench 多轮重跑批次启动"
echo "  时间戳     : $TS"
echo "  本批根目录 : $BASE_DIR"
echo "  轮次       : $ROUNDS"
echo "  Adapters   : $ADAPTERS_STR"
echo "  Preset     : $PRESET"
echo "  Questions  : $QUESTIONS"
echo "=================================================="

# ========== 防止 Mac idle 休眠（合盖仍建议别关或接电源） ==========
echo "[caffeinate] 启动 caffeinate -i -w $$ 防止空闲休眠..."
caffeinate -i -w $$ &
CAFF_PID=$!
# 只绑 EXIT：脚本因 SIGINT/SIGTERM 退出时同样会触发 EXIT，绑三个会重复打印清理日志
trap 'echo "[caffeinate] 清理 watcher (pid $CAFF_PID)"; kill $CAFF_PID 2>/dev/null || true' EXIT

# ========== adapter → venv 映射（bash 3.2 兼容，无 assoc array） ==========
get_venv_dir() {
  local adp="$1"
  if [[ "$adp" == "mem0" ]]; then
    echo ".venv-mem0"
  elif [[ "$adp" == "memoryos" ]]; then
    echo ".venv-memoryos"
  elif [[ "$adp" == "graphiti" ]]; then
    echo ".venv-graphiti"
  else
    echo ""
  fi
}

# ========== 跟踪成功/失败 ==========
declare -a SUCCESSES=()
declare -a FAILURES=()

# 切分成数组
IFS=' ' read -r -a ADAPTERS <<< "$ADAPTERS_STR"

echo ""
echo ">>> 开始执行 $ROUNDS 轮 × ${#ADAPTERS[@]} 家 adapter ..."
echo ""

# ========== 主循环：轮次 × adapter ==========
# 用 C-style 循环而非 seq：BSD seq 在 ROUNDS=0 时 `seq 0 -1` 会反向输出 0,-1（跑负轮灾难）
for ((round=0; round<ROUNDS; round++)); do
  for adapter in "${ADAPTERS[@]}"; do
    VENV_SUBDIR="$(get_venv_dir "$adapter")"
    if [[ -z "$VENV_SUBDIR" ]]; then
      echo "!!! 未知 adapter '$adapter'，跳过本轮"
      FAILURES+=("$adapter r$round (unknown adapter)")
      continue
    fi

    VENV_PY="$ROOT/$VENV_SUBDIR/bin/python"
    if [[ ! -x "$VENV_PY" ]]; then
      echo "!!! $adapter round $round 失败：venv 不存在 $VENV_PY"
      FAILURES+=("$adapter r$round (venv missing)")
      continue
    fi

    RUN_ID="${adapter}_r${round}"
    RUN_OUT_DIR="$ROOT/$BASE_DIR/$RUN_ID"

    echo ">>> [Round $round / $adapter] run-id=$RUN_ID"
    echo "    python : $VENV_SUBDIR/bin/python"
    echo "    目标目录: $BASE_DIR/$RUN_ID/"

    # 关键：--out 指向批次根，--run-id 指向叶子目录名
    # 这样 run_dir = out / run-id  恰好是 $RUN_OUT_DIR ，summary.json 直接落在叶子下
    # 注：脚本头部为 set -uo pipefail（无 -e），单家失败靠 $? 捕获即可，无需 set +e 切换
    "$VENV_PY" -m harness.run_eval \
      --questions "$QUESTIONS" \
      --out "$ROOT/$BASE_DIR" \
      --run-id "$RUN_ID" \
      --include-$adapter \
      --llm-preset "$PRESET"
    EXIT_CODE=$?

    if [[ $EXIT_CODE -ne 0 ]]; then
      echo "!!! 失败: $adapter round $round (exit=$EXIT_CODE) —— 继续下一家/下一轮"
      FAILURES+=("$adapter r$round (exit $EXIT_CODE)")
    else
      echo "    ✓ 成功: $adapter round $round"
      SUCCESSES+=("$adapter r$round")
    fi
    echo ""
  done
done

# ========== 清理 caffeinate（trap 会自动做，但显式 echo 更清晰） ==========
if kill -0 $CAFF_PID 2>/dev/null; then
  kill $CAFF_PID 2>/dev/null || true
fi

# ========== 汇总打印 ==========
NUM_SUCCESS=${#SUCCESSES[@]}
NUM_FAIL=${#FAILURES[@]}

echo "=================================================="
echo "重跑全部结束"
echo "  成功轮次 : $NUM_SUCCESS"
echo "  失败轮次 : $NUM_FAIL"
if (( NUM_FAIL > 0 )); then
  echo "失败清单："
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
fi
echo "=================================================="

# ========== 自动聚合（用项目默认 .venv） ==========
AGG_PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$AGG_PYTHON" ]]; then
  echo "警告: 项目默认 .venv 不存在 ($ROOT/.venv)，回退使用系统 python3"
  AGG_PYTHON="python3"
fi

echo ""
echo "[aggregate] 调用聚合脚本..."
"$AGG_PYTHON" "$ROOT/scripts/aggregate_runs.py" "$ROOT/$BASE_DIR"
AGG_EXIT=$?

AGG_JSON="$ROOT/$BASE_DIR/aggregate.json"

echo ""
echo "=================================================="
echo "本批根目录     : $ROOT/$BASE_DIR"
echo "成功/失败统计  : 成功 $NUM_SUCCESS / 失败 $NUM_FAIL"
if [[ -f "$AGG_JSON" ]]; then
  echo "聚合报告       : $AGG_JSON"
else
  echo "聚合报告       : 未生成 (聚合脚本 exit=$AGG_EXIT)"
fi
echo "=================================================="

# 退出码：只要有成功就算 0，全部失败才非 0（便于 CI 判断）
if (( NUM_SUCCESS > 0 )); then
  exit 0
else
  exit 1
fi
