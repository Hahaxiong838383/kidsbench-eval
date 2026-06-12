"""KidsBench Web Backend 入口（B0）。

启动：
    .venv-web/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

健康检查：
    curl http://127.0.0.1:8000/healthz

API 文档（FastAPI 内置）：
    http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, assistant_db
from .admin import router as admin_router
from .architecture import router as architecture_router
from .assistant import router as assistant_router
from .config import CORS_ORIGINS
from .events import router as events_router
from .llm_presets import router as llm_presets_router
from .questionbank import router as questionbank_router
from .runs import router as runs_router
from .state import router as state_router

# 启动时尝试加载 .env.local（本地 dev 显示 preset configured 状态）
# Production container 内通常没此文件，load_dotenv_local 返回 0 即可
try:
    from kidsbench.config import load_dotenv_local

    from .llm_presets import load_preset as _lp  # noqa: F401 - 触发 sys.path 注入

    load_dotenv_local()
except (ImportError, Exception):
    pass


app = FastAPI(
    title="KidsBench Web Backend",
    description="B0 阶段：架构白盒展示 + 状态快照 + 历史 run 列表",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(architecture_router)
app.include_router(state_router)
app.include_router(runs_router)
app.include_router(llm_presets_router)
app.include_router(events_router)
app.include_router(questionbank_router)
app.include_router(admin_router)
app.include_router(assistant_router)

# AI 助手 SQLite（手机号白名单/用量/设置）启动时建表，幂等
assistant_db.init_db()


@app.get("/healthz")
def healthz() -> dict:
    """容器编排健康检查。"""
    return {"status": "ok", "version": __version__, "phase": "B0"}


@app.get("/")
def root() -> dict:
    """根路径：列出可用 endpoint。"""
    return {
        "name": "KidsBench Web Backend",
        "version": __version__,
        "phase": "B0",
        "endpoints": {
            "health": "/healthz",
            "docs": "/docs",
            "architecture": "/api/architecture",
            "state": {
                "mem0": "/api/state/mem0",
                "memoryos": "/api/state/memoryos",
                "graphiti": "/api/state/graphiti",
            },
            "runs": {
                "list": "/api/runs",
                "detail": "/api/runs/{group}/{run_id}",
            },
        },
    }
