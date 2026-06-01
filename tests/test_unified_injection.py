"""A 决策：统一 LLM/embedding 注入校验测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kidsbench.middleware import verify_unified_injection


class _FakeAdapter:
    def __init__(self, llm: str, embed: str) -> None:
        self._llm, self._embed = llm, embed

    def get_injected_providers(self) -> dict:
        return {"internal_llm": self._llm, "internal_embed": self._embed}


def test_all_unified_ok():
    adapters = {"a": _FakeAdapter("qwen", "bge"), "b": _FakeAdapter("qwen", "bge")}
    r = verify_unified_injection(adapters, "qwen", "bge")
    assert all(x["status"] == "ok" for x in r)


def test_llm_mismatch_flagged():
    adapters = {"a": _FakeAdapter("qwen", "bge"), "b": _FakeAdapter("gpt-4o", "bge")}
    r = verify_unified_injection(adapters, "qwen", "bge")
    by = {x["adapter"]: x["status"] for x in r}
    assert by["a"] == "ok" and by["b"] == "mismatch"


def test_baseline_unknown():
    """ABC 默认 get_injected_providers={} → unknown（不参与锁定校验）。"""
    from kidsbench.adapters import NoMemoryAdapter

    r = verify_unified_injection({"nomemory": NoMemoryAdapter()}, "qwen", "bge")
    assert r[0]["status"] == "unknown"


def test_memoryos_self_reports_and_swap_true():
    """memoryos 从 config 注入 + 诚实化 swap=True（grok 纠正声明矛盾）。"""
    from kidsbench.adapters.memoryos_adapter import MemoryOSAdapter

    a = MemoryOSAdapter(config={"llm_model": "qwen-x", "embedding_model_name": "bge-x"})
    prov = a.get_injected_providers()
    assert prov["internal_llm"] == "qwen-x" and prov["internal_embed"] == "bge-x"
    # swap_supported 已诚实化为 True
    llm_dep = next(d for d in a.get_dependencies() if d.kind == "internal_llm")
    assert llm_dep.swap_supported is True
    assert llm_dep.actual_model == "qwen-x"
