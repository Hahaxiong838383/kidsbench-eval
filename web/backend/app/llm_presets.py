"""LLM Preset CRUD endpoint（Phase 3 Web UI）。

设计：
- GET  /api/llm/presets        列出所有（脱敏后）
- GET  /api/llm/presets/{name} 单个详情（脱敏）
- POST /api/llm/presets        创建自定义 preset（写 TOML 到 configs 目录）
- DELETE /api/llm/presets/{name} 删除 preset 文件

安全：
- preset 文件只存元信息（base_url / model / api_key_env 名），永不存 raw key
- raw key 由 Air 上 .env.local 提供（chmod 600 + gitignored），不接触 backend
- name 验证防路径注入
- to_public_dict 输出已脱敏（前 6 + *** + 后 4 或 <未配置>）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# 把项目 src 加进 sys.path（让 backend 复用 src/kidsbench 模块）
# 路径候选（按顺序尝试）：
# - 本地 dev：__file__ = web/backend/app/llm_presets.py，parents[3] = 项目根
# - container：/app/src/ 或 /app/kidsbench/（取决于 image 怎么 COPY）
_HERE = Path(__file__).resolve()
_SRC_CANDIDATES: list[Path] = []
# 本地 dev：__file__ 可能在 web/backend/app/，parents[3] = 项目根/src
try:
    _SRC_CANDIDATES.append(_HERE.parents[3] / "src")
except IndexError:
    pass
# container 内：src/ 已 COPY 到 /app/src
_SRC_CANDIDATES.append(Path("/app/src"))
# 兜底：kidsbench 直接放在 /app/ 下
_SRC_CANDIDATES.append(Path("/app"))

for _src in _SRC_CANDIDATES:
    if _src.is_dir() and (_src / "kidsbench").is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

try:
    from kidsbench.config import LLMPreset, list_preset_names, load_preset
    from kidsbench.config.llm_preset import DEFAULT_PRESET_DIR, _is_safe_name
except ImportError:
    # 兜底：backend 看不到 kidsbench 模块时降级（仅返回空列表）
    LLMPreset = None  # type: ignore[assignment, misc]
    list_preset_names = None  # type: ignore[assignment]
    load_preset = None  # type: ignore[assignment]
    DEFAULT_PRESET_DIR = Path("/app/configs/llm_presets")
    def _is_safe_name(n: str) -> bool:  # type: ignore[misc]
        return bool(n) and not n.startswith(".") and all(
            c.isalnum() or c in ".-_" for c in n
        )

# Preset 文件目录（可被 KIDSBENCH_PRESET_DIR env 覆盖，便于 container 挂载持久化）
PRESET_DIR = Path(
    os.environ.get("KIDSBENCH_PRESET_DIR", str(DEFAULT_PRESET_DIR))
).expanduser()

router = APIRouter(prefix="/api/llm", tags=["llm"])


# ============================================================
# Schema
# ============================================================


class EmbeddingSchema(BaseModel):
    provider: str = "huggingface"
    model: str = "BAAI/bge-small-zh-v1.5"
    dim: int = 512


class CreatePresetSchema(BaseModel):
    """创建 preset 的请求体（永不接收 raw api_key，只接收 env_var 名）。"""

    name: str = Field(min_length=1, max_length=64)
    display_name: str | None = None
    provider: str = "custom"
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    model: str = Field(min_length=1)
    max_tokens: int = 4096
    reasoning_effort: str | None = None
    embedding: EmbeddingSchema = Field(default_factory=EmbeddingSchema)


# ============================================================
# Endpoints
# ============================================================


@router.get("/presets")
def list_presets_endpoint() -> dict:
    """列出所有 preset（脱敏）。"""
    if list_preset_names is None or load_preset is None:
        return {"items": [], "preset_dir": str(PRESET_DIR), "error": "kidsbench config 模块不可用"}
    items = []
    for name in list_preset_names(PRESET_DIR):
        try:
            p = load_preset(name, PRESET_DIR)
            items.append(p.to_public_dict())
        except Exception as exc:
            items.append({"name": name, "error": str(exc)})
    return {"items": items, "preset_dir": str(PRESET_DIR), "count": len(items)}


@router.get("/presets/{name}")
def get_preset(name: str) -> dict:
    if not _is_safe_name(name):
        raise HTTPException(status_code=400, detail="invalid preset name")
    if load_preset is None:
        raise HTTPException(status_code=503, detail="kidsbench config 模块不可用")
    try:
        p = load_preset(name, PRESET_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return p.to_public_dict()


@router.post("/presets", status_code=201)
def create_preset(body: CreatePresetSchema) -> dict:
    """创建自定义 preset。

    永不接收 raw api_key；前端只传 api_key_env 名。
    用户需要自己在 Air 上的 .env.local 里设置对应 KEY=value。
    """
    if not _is_safe_name(body.name):
        raise HTTPException(
            status_code=400,
            detail="name 只能含字母数字 . - _，不能以 . 开头",
        )

    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    path = PRESET_DIR / f"{body.name}.toml"
    if path.exists():
        raise HTTPException(status_code=409, detail=f"preset '{body.name}' 已存在")

    toml_lines = [
        f'name = "{_escape(body.name)}"',
        f'display_name = "{_escape(body.display_name or body.name)}"',
        f'provider = "{_escape(body.provider)}"',
        f'base_url = "{_escape(body.base_url)}"',
        f'api_key_env = "{_escape(body.api_key_env)}"',
        f'model = "{_escape(body.model)}"',
        f"max_tokens = {body.max_tokens}",
    ]
    if body.reasoning_effort:
        toml_lines.append(f'reasoning_effort = "{_escape(body.reasoning_effort)}"')
    toml_lines.extend([
        "",
        "[embedding]",
        f'provider = "{_escape(body.embedding.provider)}"',
        f'model = "{_escape(body.embedding.model)}"',
        f"dim = {body.embedding.dim}",
    ])
    content = "\n".join(toml_lines) + "\n"
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o644)
    except OSError:
        pass

    # 加载回来验证 + 返回脱敏视图
    if load_preset is not None:
        p = load_preset(body.name, PRESET_DIR)
        return p.to_public_dict()
    return {"name": body.name, "created": True, "path": str(path)}


@router.delete("/presets/{name}")
def delete_preset(name: str) -> dict:
    if not _is_safe_name(name):
        raise HTTPException(status_code=400, detail="invalid preset name")
    path = PRESET_DIR / f"{name}.toml"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"preset '{name}' not found")
    path.unlink()
    return {"name": name, "deleted": True}


# ============================================================
# Helper
# ============================================================


def _escape(s: str) -> str:
    """TOML string 转义（最小化处理：替换反斜杠 + 双引号）"""
    return s.replace("\\", "\\\\").replace('"', '\\"')
