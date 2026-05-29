"""LLM Preset 单测。"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from kidsbench.config import (
    LLMPreset,
    list_preset_names,
    list_presets,
    load_dotenv_local,
    load_preset,
)


def _write_preset(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / f"{name}.toml"
    f.write_text(textwrap.dedent(content), encoding="utf-8")
    return f


@pytest.fixture
def tmp_preset_dir(tmp_path):
    _write_preset(tmp_path, "foo-llm", """
        name = "foo-llm"
        display_name = "Foo LLM"
        provider = "foo"
        base_url = "https://foo.api/v1/"
        api_key_env = "FOO_API_KEY"
        model = "foo-1"
        max_tokens = 8192
        reasoning_effort = "low"

        [embedding]
        provider = "huggingface"
        model = "BAAI/bge-base-zh"
        dim = 768
    """)
    _write_preset(tmp_path, "bar-llm", """
        base_url = "https://bar.com/v1"
        api_key_env = "BAR_KEY"
        model = "bar-x"
    """)
    return tmp_path


def test_list_preset_names(tmp_preset_dir):
    names = list_preset_names(tmp_preset_dir)
    assert names == ["bar-llm", "foo-llm"]


def test_load_preset_full(tmp_preset_dir):
    preset = load_preset("foo-llm", tmp_preset_dir)
    assert preset.name == "foo-llm"
    assert preset.display_name == "Foo LLM"
    assert preset.provider == "foo"
    assert preset.base_url == "https://foo.api/v1"  # 尾斜杠 strip
    assert preset.api_key_env == "FOO_API_KEY"
    assert preset.model == "foo-1"
    assert preset.max_tokens == 8192
    assert preset.reasoning_effort == "low"
    assert preset.embedding.dim == 768


def test_load_preset_minimal_defaults(tmp_preset_dir):
    preset = load_preset("bar-llm", tmp_preset_dir)
    assert preset.name == "bar-llm"
    assert preset.display_name == "bar-llm"  # 默认用 name
    assert preset.max_tokens == 4096  # 默认值
    assert preset.embedding.dim == 512  # 默认值


def test_load_preset_unknown_raises(tmp_preset_dir):
    with pytest.raises(ValueError, match="unknown preset"):
        load_preset("nonexistent", tmp_preset_dir)


def test_load_preset_invalid_name():
    with pytest.raises(ValueError, match="invalid preset name"):
        load_preset("../../../etc/passwd")
    with pytest.raises(ValueError, match="invalid preset name"):
        load_preset(".hidden")


def test_load_preset_missing_required_raises(tmp_path):
    _write_preset(tmp_path, "broken", """
        name = "broken"
        # 缺 base_url / api_key_env / model
    """)
    with pytest.raises(ValueError, match="缺字段"):
        load_preset("broken", tmp_path)


def test_get_api_key_from_env(tmp_preset_dir, monkeypatch):
    monkeypatch.setenv("FOO_API_KEY", "sk-real-key-1234567890")
    preset = load_preset("foo-llm", tmp_preset_dir)
    assert preset.get_api_key() == "sk-real-key-1234567890"
    assert preset.is_configured() is True


def test_get_api_key_missing_raises(tmp_preset_dir, monkeypatch):
    monkeypatch.delenv("FOO_API_KEY", raising=False)
    preset = load_preset("foo-llm", tmp_preset_dir)
    with pytest.raises(RuntimeError, match="FOO_API_KEY"):
        preset.get_api_key()
    assert preset.is_configured() is False


def test_api_key_masked(tmp_preset_dir, monkeypatch):
    # 完整 key
    monkeypatch.setenv("FOO_API_KEY", "sk-abcdef1234567890xyz")
    preset = load_preset("foo-llm", tmp_preset_dir)
    masked = preset.get_api_key_masked()
    assert masked.startswith("sk-abc")
    assert masked.endswith("0xyz")
    assert "abcdef" in preset.get_api_key()  # 真 key 仍含中间
    assert "abcdef" not in masked  # 脱敏后中间被星号替换

    # 过短 key
    monkeypatch.setenv("FOO_API_KEY", "abc")
    preset_short = load_preset("foo-llm", tmp_preset_dir)
    assert preset_short.get_api_key_masked() == "***"

    # 未设
    monkeypatch.delenv("FOO_API_KEY")
    assert preset.get_api_key_masked() == "<未配置>"


def test_to_public_dict_no_raw_key(tmp_preset_dir, monkeypatch):
    """to_public_dict 永不包含 raw api_key（HTTP API 安全)"""
    monkeypatch.setenv("FOO_API_KEY", "sk-secret-12345678abc")
    preset = load_preset("foo-llm", tmp_preset_dir)
    d = preset.to_public_dict()
    # 不允许 raw key 出现
    raw_key = "sk-secret-12345678abc"
    assert raw_key not in str(d)
    assert d["api_key_masked"].startswith("sk-sec")
    assert d["configured"] is True


def test_list_presets_loads_all(tmp_preset_dir):
    presets = list_presets(tmp_preset_dir)
    assert len(presets) == 2
    assert all(isinstance(p, LLMPreset) for p in presets)


# ============================================================
# .env.local 加载
# ============================================================


def test_load_dotenv_local_injects(tmp_path, monkeypatch):
    monkeypatch.delenv("KIDSBENCH_TEST_KEY1", raising=False)
    monkeypatch.delenv("KIDSBENCH_TEST_KEY2", raising=False)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        textwrap.dedent(
            '''
            # 这是注释，会被忽略
            KIDSBENCH_TEST_KEY1=value1
            KIDSBENCH_TEST_KEY2="quoted value"

            INVALID_LINE_NO_EQ
            '''
        ),
        encoding="utf-8",
    )
    n = load_dotenv_local(env_file)
    assert n == 2
    assert os.environ["KIDSBENCH_TEST_KEY1"] == "value1"
    assert os.environ["KIDSBENCH_TEST_KEY2"] == "quoted value"


def test_load_dotenv_local_does_not_override(tmp_path, monkeypatch):
    """已有 env 的值不会被 .env.local 覆盖"""
    monkeypatch.setenv("KIDSBENCH_PRESET_OVERRIDE", "from_shell")
    env_file = tmp_path / ".env.local"
    env_file.write_text("KIDSBENCH_PRESET_OVERRIDE=from_file\n", encoding="utf-8")
    load_dotenv_local(env_file)
    assert os.environ["KIDSBENCH_PRESET_OVERRIDE"] == "from_shell"


def test_load_dotenv_local_missing_file_safe(tmp_path):
    """文件不存在不抛错，返回 0"""
    assert load_dotenv_local(tmp_path / "doesnt-exist") == 0
