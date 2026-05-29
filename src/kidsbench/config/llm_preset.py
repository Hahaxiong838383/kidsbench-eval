"""LLM Preset 加载与脱敏访问。

设计原则：
- Preset 是 TOML 文件，含 base_url / model / api_key_env 等元信息
- API key **永不**写进 preset 文件，只通过 env 注入（.env.local / 环境变量）
- get_api_key() 拿真实 key，get_api_key_masked() 拿脱敏值（前端展示用）
- preset 文件入 git；.env.local 不入 git（在 .gitignore）

按 CLAUDE.md：
- 不可变（dataclass frozen=True）
- 错误处理（preset 找不到 / key 缺失 时显式抛 RuntimeError）
- 输入验证（path 注入 / yaml 注入等已用 stem + tomllib 防御）
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# 项目根（src/kidsbench/config/ 上推 3 级）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRESET_DIR = _PROJECT_ROOT / "configs" / "llm_presets"
DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env.local"


@dataclass(frozen=True)
class EmbeddingConfig:
    """嵌入模型配置。"""

    provider: str  # huggingface / openai / etc
    model: str
    dim: int


@dataclass(frozen=True)
class LLMPreset:
    """LLM 配置 preset。

    用法：
        preset = load_preset("deepseek-v4-pro")
        client = openai.OpenAI(
            base_url=preset.base_url,
            api_key=preset.get_api_key(),  # 抛 RuntimeError 如果 env 未设
        )
    """

    name: str
    display_name: str
    provider: str
    base_url: str
    api_key_env: str
    model: str
    max_tokens: int
    reasoning_effort: str | None
    embedding: EmbeddingConfig
    extra: dict[str, Any] = field(default_factory=dict)

    def get_api_key(self) -> str:
        """从 env 读 API key。读不到抛 RuntimeError。"""
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"LLM preset '{self.name}' 需要 env '{self.api_key_env}'，"
                f"请在 .env.local 或环境变量里设置（不要写进 preset 文件）。\n"
                f"  echo '{self.api_key_env}=sk-xxx' >> {DEFAULT_ENV_FILE}\n"
                f"  chmod 600 {DEFAULT_ENV_FILE}"
            )
        return key

    def get_api_key_masked(self) -> str:
        """脱敏的 key 展示用（前端 / 日志）。

        前 6 + *** + 后 4。env 未设时返回 '<未配置>'。
        """
        try:
            key = self.get_api_key()
        except RuntimeError:
            return "<未配置>"
        if len(key) < 12:
            return "***"
        return key[:6] + "***" + key[-4:]

    def is_configured(self) -> bool:
        """API key env 是否已设置（前端筛选可用 preset 用）。"""
        return bool(os.environ.get(self.api_key_env, "").strip())

    def to_public_dict(self) -> dict[str, Any]:
        """脱敏后的字典（HTTP API 安全返回，永不包含真 key）。"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key_masked": self.get_api_key_masked(),
            "model": self.model,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
            "configured": self.is_configured(),
            "embedding": {
                "provider": self.embedding.provider,
                "model": self.embedding.model,
                "dim": self.embedding.dim,
            },
            "extra": self.extra,
        }


# ============================================================
# Loader
# ============================================================


def load_preset(name: str, preset_dir: Path | None = None) -> LLMPreset:
    """按 name 加载 preset。name 同文件名 stem。"""
    preset_dir = preset_dir or DEFAULT_PRESET_DIR
    # name 防注入：必须是合法标识符 + 短横线 + 点
    if not _is_safe_name(name):
        raise ValueError(f"invalid preset name '{name}'")

    path = preset_dir / f"{name}.toml"
    if not path.is_file():
        available = list_preset_names(preset_dir)
        raise ValueError(
            f"unknown preset '{name}' at {path}. Available: {available}"
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    return _parse(data, default_name=name)


def list_preset_names(preset_dir: Path | None = None) -> list[str]:
    """列出所有可用 preset 名（按字母排序）。"""
    preset_dir = preset_dir or DEFAULT_PRESET_DIR
    if not preset_dir.is_dir():
        return []
    return sorted(p.stem for p in preset_dir.glob("*.toml"))


def list_presets(preset_dir: Path | None = None) -> list[LLMPreset]:
    """加载所有 preset（前端列表 / web API 用）。"""
    return [load_preset(n, preset_dir) for n in list_preset_names(preset_dir)]


def _is_safe_name(name: str) -> bool:
    """preset 名只允许 [a-zA-Z0-9.-_]"""
    if not name or name.startswith("."):
        return False
    return all(c.isalnum() or c in ".-_" for c in name)


def _parse(data: dict[str, Any], default_name: str) -> LLMPreset:
    """从 dict 构造 LLMPreset。校验必填字段。"""
    required = {"base_url", "api_key_env", "model"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"preset '{default_name}' 缺字段: {sorted(missing)}")

    emb_data = data.get("embedding") or {}
    embedding = EmbeddingConfig(
        provider=str(emb_data.get("provider", "huggingface")),
        model=str(emb_data.get("model", "BAAI/bge-small-zh-v1.5")),
        dim=int(emb_data.get("dim", 512)),
    )

    # 其他未识别字段统一塞 extra（前向兼容未来新字段）
    known_keys = {
        "name", "display_name", "provider", "base_url", "api_key_env",
        "model", "max_tokens", "reasoning_effort", "embedding",
    }
    extra = {k: v for k, v in data.items() if k not in known_keys}

    return LLMPreset(
        name=str(data.get("name", default_name)),
        display_name=str(data.get("display_name", data.get("name", default_name))),
        provider=str(data.get("provider", "unknown")),
        base_url=str(data["base_url"]).rstrip("/"),
        api_key_env=str(data["api_key_env"]),
        model=str(data["model"]),
        max_tokens=int(data.get("max_tokens", 4096)),
        reasoning_effort=data.get("reasoning_effort"),
        embedding=embedding,
        extra=extra,
    )


# ============================================================
# .env.local 加载（启动时调一次）
# ============================================================


def load_dotenv_local(path: Path | None = None) -> int:
    """读 .env.local 文件并注入 os.environ（如果 env 未设的话）。

    格式：
        KEY=value
        KEY2="quoted value"
        # 注释行
        # 已有的 env 不会被覆盖（用户 export 优先）

    返回：注入了多少个变量
    """
    path = path or DEFAULT_ENV_FILE
    if not path.is_file():
        return 0
    injected = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            # 不覆盖已有 env（避免坑用户 export）
            if key in os.environ and os.environ[key]:
                continue
            os.environ[key] = value
            injected += 1
    except OSError:
        return 0
    return injected
