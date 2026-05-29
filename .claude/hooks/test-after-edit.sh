#!/bin/bash
# KidsBench 项目：写完代码自动跑相关测试
#
# 触发：PostToolUse on Edit | Write | MultiEdit
# 行为：根据被改的文件路径，选择性跑测试 / lint
#
# 设计原则：
# - 只对 kidsbench-eval 项目的文件触发（跨项目 Edit 不打扰）
# - 失败不阻塞（exit 0），仅打印警告到 stderr
# - 测试要快（<3s），不要拖慢 inline 节奏
# - 跑过的测试集合明确告诉 cc，让他知道交付被验证了

set +e   # 不因测试失败而阻塞 hook

INPUT="$(cat 2>/dev/null || true)"

# 提取被改的文件路径（jq 优先，jq 缺失时 grep fallback）
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
else
  FILE_PATH=$(echo "$INPUT" | grep -oE '"file_path"\s*:\s*"[^"]+"' | head -1 | sed -E 's/.*"file_path"\s*:\s*"([^"]+)".*/\1/')
fi

# 不是 kidsbench-eval 文件 → 跳过
if [ -z "$FILE_PATH" ] || [[ "$FILE_PATH" != *kidsbench-eval* ]]; then
  exit 0
fi

PROJECT="/Users/rayman.chen/mycc/kidsbench-eval"
TS=$(date +%H:%M:%S)

case "$FILE_PATH" in

  # ─── Backend Python（web/backend/app/*.py 或 tests/*.py）
  *kidsbench-eval/web/backend/*.py)
    echo "[hook ${TS}] backend python 改动 → pytest" >&2
    cd "$PROJECT/web/backend" || exit 0
    OUTPUT=$(PYTHONPATH=. "$PROJECT/.venv-web/bin/pytest" tests/ -q --tb=line 2>&1)
    LAST=$(echo "$OUTPUT" | tail -3)
    if echo "$OUTPUT" | grep -qE "(failed|error)"; then
      echo "[hook ${TS}] ❌ backend pytest 有失败：" >&2
      echo "$LAST" >&2
    else
      echo "[hook ${TS}] ✅ backend pytest: $(echo "$LAST" | tail -1)" >&2
    fi
    ;;

  # ─── Frontend TS/TSX（依赖 vite HMR 实时检查，hook 跑 tsc 太慢，仅做存在性 / import 抽查）
  *kidsbench-eval/web/frontend/src/*.tsx | *kidsbench-eval/web/frontend/src/*.ts)
    # tsc 全量太慢（2s+），由 vite dev server HMR 实时报错就够
    # 这里仅快速 sanity check：grep 看有没有明显语法错（unclosed paren / brace）
    if [ -f "$FILE_PATH" ]; then
      OPEN_BRACE=$(grep -c '{' "$FILE_PATH" 2>/dev/null || echo 0)
      CLOSE_BRACE=$(grep -c '}' "$FILE_PATH" 2>/dev/null || echo 0)
      if [ "$OPEN_BRACE" != "$CLOSE_BRACE" ]; then
        echo "[hook ${TS}] ⚠ frontend $FILE_PATH 大括号不配对（${OPEN_BRACE} vs ${CLOSE_BRACE}），检查" >&2
      fi
    fi
    ;;

  # ─── Core src/kidsbench/（adapter / contract / middleware 等核心模块）
  *kidsbench-eval/src/kidsbench/*.py)
    echo "[hook ${TS}] core 模块改动 → pytest core" >&2
    cd "$PROJECT" || exit 0
    # 用 .venv-mem0 跑核心契约测试（最快）
    if [ -d ".venv-mem0" ] && [ -d "tests" ]; then
      OUTPUT=$(PYTHONPATH=src .venv-mem0/bin/pytest tests/ -q --tb=line -x --timeout=30 2>&1)
      LAST=$(echo "$OUTPUT" | tail -3)
      if echo "$OUTPUT" | grep -qE "(failed|error)"; then
        echo "[hook ${TS}] ❌ core pytest 有失败：" >&2
        echo "$LAST" >&2
      else
        echo "[hook ${TS}] ✅ core pytest: $(echo "$LAST" | tail -1)" >&2
      fi
    fi
    ;;

  # ─── SPEC / 文档 / hook 自身 → 不跑测试
  *kidsbench-eval/docs/*.md | *kidsbench-eval/README.md | *kidsbench-eval/.claude/*)
    # 文档改动无需跑测试
    ;;

  # ─── 其他 kidsbench-eval 文件（pyproject.toml / questions/*.jsonl 等）→ 不跑
  *)
    ;;
esac

exit 0
