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

# runs/ 目录（历史 run 数据）—— 多数据源注册
#
# 背景：评测引擎从 Air 迁到工作站 dev198，两台机器各自产出 eval 结果。
# web 平台需在两个数据源间切换查看各自的 runs，零改磁盘数据。
#
# 向后兼容：旧 env KIDSBENCH_RUNS_PATH 若设置，作为 air 源的默认值，
# 这样旧部署不动 env 也能正常跑（air 即原单源行为）。
_LEGACY_RUNS_PATH = os.environ.get("KIDSBENCH_RUNS_PATH")
_AIR_DEFAULT = (
    Path(_LEGACY_RUNS_PATH).expanduser().resolve()
    if _LEGACY_RUNS_PATH
    else (PROJECT_ROOT / "runs").resolve()
)

RUNS_SOURCES: dict[str, Path] = {
    "air": _env_path("KIDSBENCH_RUNS_PATH_AIR", _AIR_DEFAULT),
    "dev198": _env_path("KIDSBENCH_RUNS_PATH_DEV198", PROJECT_ROOT / "runs-dev198"),
}

# 默认源（不传 source 时用它，保持旧行为 = air）
DEFAULT_SOURCE: str = os.environ.get("KIDSBENCH_DEFAULT_SOURCE", "air")
if DEFAULT_SOURCE not in RUNS_SOURCES:
    DEFAULT_SOURCE = "air"

# 向后兼容：旧代码直接 import RUNS_PATH 的，等于默认源
RUNS_PATH: Path = RUNS_SOURCES.get(DEFAULT_SOURCE, RUNS_SOURCES["air"])


def runs_path_for(source: str | None) -> Path:
    """按 source 取 runs 目录；未知/None 回退默认源。"""
    return RUNS_SOURCES.get(source or DEFAULT_SOURCE, RUNS_SOURCES[DEFAULT_SOURCE])

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
