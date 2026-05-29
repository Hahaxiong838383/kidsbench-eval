"""集中配置。

按 CLAUDE.md：避免硬编码，所有路径 / 端点统一从 env 读，给合理默认。
"""

import os
from pathlib import Path


def _env_path(key: str, default: Path) -> Path:
    """从 env 读路径，默认值 + 解析为绝对路径。"""
    value = os.environ.get(key)
    return Path(value).expanduser().resolve() if value else default.resolve()


# 项目根
# - Air 本地：web/backend/app/config.py → parents[3] = kidsbench-eval/
# - QNAP 容器内：/app/app/config.py 路径不够深 → fallback 到 /app
# 真实路径以 env (KIDSBENCH_RUNS_PATH 等) 覆盖为准，PROJECT_ROOT 只是 fallback
try:
    PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
except IndexError:
    PROJECT_ROOT = Path("/app")

# runs/ 目录（历史 run 数据）
RUNS_PATH: Path = _env_path("KIDSBENCH_RUNS_PATH", PROJECT_ROOT / "runs")

# questions/ 目录（B0 阶段读 jsonl，B2 阶段引入 SQLite）
QUESTIONS_PATH: Path = _env_path("KIDSBENCH_QUESTIONS_PATH", PROJECT_ROOT / "questions")

# 项目 src/ 路径（前端代码索引时拼路径）
SRC_PATH: Path = _env_path("KIDSBENCH_SRC_PATH", PROJECT_ROOT / "src")

# FalkorDB 连接（Graphiti 用，B0 唯一实时拉的存储）
FALKOR_HOST: str = os.environ.get("FALKOR_HOST", "127.0.0.1")
FALKOR_PORT: int = int(os.environ.get("FALKOR_PORT", "16379"))

# Qdrant 连接（mem0 用，B0 阶段从 final.json 拉 stats，不直连，留这里给 B1+）
QDRANT_HOST: str = os.environ.get("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT: int = int(os.environ.get("QDRANT_PORT", "6333"))

# CORS（开发期开放，部署后由 nginx 同源处理）
CORS_ORIGINS: list[str] = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")
