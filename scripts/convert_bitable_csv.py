"""飞书题库 CSV → 执行层 jsonl 转换器（CLI 壳）。

核心逻辑在 src/kidsbench/questionbank/converter.py（web 上传入口共用同一实现）。
机制说明（人话版）见 docs/CONVERSION_PIPELINE.md。

用法:  python scripts/convert_bitable_csv.py [--csv PATH] [--out-dir questions]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kidsbench.questionbank.converter import convert  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default="questions/raw/v01_memory_20260611.csv")
    ap.add_argument("--out-dir", default="questions")
    ap.add_argument("--patches", default="questions/patches/v01_memory_patches.json")
    ap.add_argument("--hypotheses",
                    default="questions/patches/v01_memory_hypotheses.json")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"❌ CSV 不存在: {csv_path}", file=sys.stderr)
        return 1

    result = convert(csv_path, Path(args.out_dir), Path(args.patches),
                     Path(args.hypotheses))

    from collections import Counter
    kinds = Counter(i.kind for i in result.issues)
    print(f"✅ 转换完成: {len(result.questions)} 题进入 jsonl"
          f"（其中打补丁修复 {len([q for q in result.questions if q['patched']])} 题）")
    print(f"🩹 应用补丁: {len(result.patched_qids)} 题")
    print(f"➡️  重标移出记忆轨: {len(result.reclassified)} 题 {result.reclassified}")
    print(f"⏭️  跳过(非④⑤主测): {len(result.skipped)} 题")
    print(f"⚠️  剩余问题: {len(result.issues)} 条 / 涉及 "
          f"{len({i.qid for i in result.issues})} 题")
    for kind, n in kinds.most_common():
        print(f"   - {kind}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
