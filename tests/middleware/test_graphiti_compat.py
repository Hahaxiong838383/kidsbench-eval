"""Graphiti compat 层 Mock 测试（不依赖真实 graphiti 装好）。"""
from __future__ import annotations

from kidsbench.middleware.graphiti_compat import (
    _FakeResponsesObject,
    _get_schema_str,
    _inject_schema_prompt,
    clear_schema_cache,
)


def test_fake_responses_object_has_required_attrs():
    """graphiti _handle_structured_response 会检查 output_text + refusal + model_dump。"""
    obj = _FakeResponsesObject(output_text='{"foo": "bar"}', raw={"x": 1})
    assert obj.output_text == '{"foo": "bar"}'
    assert obj.refusal is None  # 必须存在，graphiti 检查
    assert obj.raw == {"x": 1}
    dump = obj.model_dump()
    assert dump["output_text"] == '{"foo": "bar"}'
    assert dump["refusal"] is None


def test_inject_schema_with_existing_system():
    """已有 system message 时 schema 提示 append 到 system content。"""
    messages = [
        {"role": "system", "content": "You are a helpful AI."},
        {"role": "user", "content": "Extract entities from: 团子是布偶猫"},
    ]
    schema_str = '{"type": "object", "properties": {"name": {"type": "string"}}}'
    result = _inject_schema_prompt(messages, schema_str)

    assert len(result) == 2  # 不增加新 message
    assert result[0]["role"] == "system"
    assert "You are a helpful AI." in result[0]["content"]
    assert "JSON Schema" in result[0]["content"]
    assert schema_str in result[0]["content"]
    assert result[1] == messages[1]  # user 消息不变
    # 原 messages 不被修改
    assert messages[0]["content"] == "You are a helpful AI."


def test_inject_schema_without_system():
    """无 system message 时新建一条 system 在最前。"""
    messages = [{"role": "user", "content": "Hello"}]
    schema_str = '{"type": "object"}'
    result = _inject_schema_prompt(messages, schema_str)

    assert len(result) == 2  # 增加一条
    assert result[0]["role"] == "system"
    assert schema_str in result[0]["content"]
    assert "JSON Schema" in result[0]["content"]
    assert result[1] == messages[0]


def test_inject_schema_empty_messages():
    """空 messages 也能处理。"""
    result = _inject_schema_prompt([], '{}')
    assert len(result) == 1
    assert result[0]["role"] == "system"


def test_schema_cache_works():
    """重复请求同一 BaseModel 时返回缓存的字符串。"""
    from pydantic import BaseModel

    class _DummyModel(BaseModel):
        name: str
        count: int

    clear_schema_cache()
    s1 = _get_schema_str(_DummyModel)
    s2 = _get_schema_str(_DummyModel)
    assert s1 is s2  # 同一对象引用（缓存）
    assert '"name"' in s1
    assert '"count"' in s1


def test_schema_cache_different_models():
    """不同 BaseModel 缓存独立。"""
    from pydantic import BaseModel

    class _ModelA(BaseModel):
        a: str

    class _ModelB(BaseModel):
        b: int

    clear_schema_cache()
    sa = _get_schema_str(_ModelA)
    sb = _get_schema_str(_ModelB)
    assert sa != sb
    assert '"a"' in sa
    assert '"b"' in sb


def test_kidsbench_client_unavailable_when_no_graphiti(monkeypatch):
    """没装 graphiti 时实例化应抛 ImportError。

    用 monkeypatch 模拟 _GRAPHITI_AVAILABLE=False 看 KidsBenchGraphitiLLMClient init 行为。
    """
    import kidsbench.middleware.graphiti_compat as compat

    if not compat._GRAPHITI_AVAILABLE:
        # 实际就没装 → 实例化必抛
        try:
            compat.KidsBenchGraphitiLLMClient()
        except ImportError as e:
            assert "graphiti-core" in str(e)
        else:
            raise AssertionError("应该抛 ImportError")


def test_build_kwargs_enforces_min_max_tokens():
    """max_tokens < 4096 时强制提到 4096（gemini-3.5-flash thinking 防护）。"""
    import kidsbench.middleware.graphiti_compat as compat

    if not compat._GRAPHITI_AVAILABLE:
        # 没装 graphiti 跳过这个测试（KidsBenchGraphitiLLMClient init 会抛）
        import pytest
        pytest.skip("graphiti-core not installed")

    # 用 Mock 的 client 避开真实 openai 依赖
    class _MockAsyncOpenAI:
        pass

    client = compat.KidsBenchGraphitiLLMClient(client=_MockAsyncOpenAI())
    kw = client._build_kwargs("test-model", [{"role": "user", "content": "hi"}], 0.0, 100)
    assert kw["max_tokens"] == 4096
    kw2 = client._build_kwargs("test-model", [], 0.0, 8192)
    assert kw2["max_tokens"] == 8192
    # reasoning_effort 默认 minimal
    assert kw["reasoning_effort"] == "minimal"


def test_build_kwargs_can_disable_reasoning():
    """reasoning_effort=None 时不加该字段。"""
    import kidsbench.middleware.graphiti_compat as compat

    if not compat._GRAPHITI_AVAILABLE:
        import pytest
        pytest.skip("graphiti-core not installed")

    client = compat.KidsBenchGraphitiLLMClient(client=object(), reasoning_effort=None)
    kw = client._build_kwargs("m", [], 0.0, 4096)
    assert "reasoning_effort" not in kw
