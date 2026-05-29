#!/bin/bash
# KidsBench Stop hook：session 结束前提示是否要落盘
#
# 触发：Stop event（session 自然结束 / 用户清屏 / cc compact）
# 行为：检查 kidsbench-eval 是否有 uncommitted 改动，有则提示 cc 跑 /kidsbench-landing
# 不强制：只 stderr 输出提醒，cc 自己判断是否真要落盘

set +e

PROJECT=/Users/rayman.chen/mycc/kidsbench-eval
[ ! -d "$PROJECT/.git" ] && exit 0

cd "$PROJECT" || exit 0

CHANGES=$(git status --porcelain | wc -l | tr -d ' ')
[ "$CHANGES" = "0" ] && exit 0

# 区分 staged / unstaged / untracked
STAGED=$(git diff --cached --numstat | wc -l | tr -d ' ')
UNSTAGED=$(git diff --numstat | wc -l | tr -d ' ')
UNTRACKED=$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')

# 看 untracked 里有没有 dist/.venv-/node_modules 等本来不该入库的，过滤掉
REAL_UNTRACKED=$(git ls-files --others --exclude-standard | grep -vE '(node_modules|\.venv-|dist/|runs/|__pycache__)' | wc -l | tr -d ' ')

echo "" >&2
echo "─────────────────────────────────────────────────" >&2
echo "[stop-hook] KidsBench 有 ${CHANGES} 个变化未提交：" >&2
[ "$STAGED" != "0" ] && echo "  · staged:    $STAGED 个" >&2
[ "$UNSTAGED" != "0" ] && echo "  · unstaged:  $UNSTAGED 个" >&2
[ "$REAL_UNTRACKED" != "0" ] && echo "  · untracked: $REAL_UNTRACKED 个（已过滤 venv/dist/runs）" >&2
echo "" >&2
echo "  考虑跑 /kidsbench-landing 落盘（pytest + commit + push + memory 更新）" >&2
echo "─────────────────────────────────────────────────" >&2

exit 0
