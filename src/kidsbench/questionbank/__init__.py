"""题库转换管线（协议 v1.1）。

核心逻辑在 converter.py，被两处复用：
- scripts/convert_bitable_csv.py（CLI，本地跑）
- web/backend/app/questionbank.py（web 上传入口，在线跑）

机制说明（人话版）见 docs/CONVERSION_PIPELINE.md。
"""

from .converter import (
    ConvertResult,
    Issue,
    convert,
    load_patches,
    merge_hypotheses,
    parse_history,
)

__all__ = [
    "ConvertResult",
    "Issue",
    "convert",
    "load_patches",
    "merge_hypotheses",
    "parse_history",
]
