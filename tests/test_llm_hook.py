"""trace.llm_hook 单测（B1.1 第二刀）。

覆盖：
- 通用 _wrap_sync / _wrap_async 装饰逻辑（用 fake class，不依赖 openai/st 真装上）
- install / uninstall 幂等 + 完全还原
- attrs extractor 各种边界（dict / object / 空）
- 不 init_run 时方法直接透传
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kidsbench.trace import (
    Exporter,
    SpanEvent,
    init_run,
    set_exporter,
)
from kidsbench.trace.llm_hook import (
    PATCH_MARKER,
    _extract_chat_attrs,
    _extract_embedding_attrs,
    _extract_st_attrs,
    _make_async_wrapper,
    _make_sync_wrapper,
    _safe_patch_method,
    install,
    installed_methods,
    is_installed,
    uninstall,
)


class CollectingExporter(Exporter):
    def __init__(self) -> None:
        self.events: list[SpanEvent] = []

    def export(self, event: SpanEvent) -> None:
        self.events.append(event)


@pytest.fixture
def collector():
    c = CollectingExporter()
    set_exporter(c)
    yield c
    set_exporter(None)


# ============================================================
# _wrap_sync 行为
# ============================================================


def test_patch_method_basic(collector):
    class Fake:
        def method(self, x: int) -> int:
            return x * 2

    _safe_patch_method(
        Fake, "method", "test.fake", _make_sync_wrapper, lambda a, k, r: {"result": r}
    )
    try:
        inst = Fake()
        with init_run("r_basic"):
            assert inst.method(5) == 10
        names = [e.name for e in collector.events]
        assert "test.fake" in names
        attr_events = [e for e in collector.events if e.type == "ATTR"]
        assert any(e.attrs.get("result") == 10 for e in attr_events)
    finally:
        _force_restore(Fake, "method")


def test_patch_method_no_trace_passthrough(collector):
    class Fake:
        def method(self, x: int) -> int:
            return x + 100

    _safe_patch_method(
        Fake, "method", "test.fake2", _make_sync_wrapper, lambda a, k, r: {"x": r}
    )
    try:
        inst = Fake()
        assert inst.method(7) == 107
        assert len([e for e in collector.events if e.name == "test.fake2"]) == 0
    finally:
        _force_restore(Fake, "method")


def test_patch_method_exception_propagates(collector):
    class Fake:
        def boom(self) -> None:
            raise ValueError("kaboom")

    _safe_patch_method(
        Fake, "boom", "test.boom", _make_sync_wrapper, lambda a, k, r: {}
    )
    try:
        inst = Fake()
        with init_run("r_boom"):
            with pytest.raises(ValueError, match="kaboom"):
                inst.boom()
        exits = [
            e for e in collector.events if e.name == "test.boom" and e.type == "EXIT"
        ]
        assert len(exits) == 1
        assert "kaboom" in exits[0].attrs.get("error", "")
    finally:
        _force_restore(Fake, "boom")


def test_patch_method_extract_failure_isolated(collector):
    class Fake:
        def method(self) -> int:
            return 42

    def bad_extract(a, k, r):
        raise RuntimeError("extract broken")

    _safe_patch_method(
        Fake, "method", "test.bad_extract", _make_sync_wrapper, bad_extract
    )
    try:
        inst = Fake()
        with init_run("r_bad"):
            assert inst.method() == 42
        names = [e.name for e in collector.events if e.type in ("ENTER", "EXIT")]
        assert names.count("test.bad_extract") == 2
    finally:
        _force_restore(Fake, "method")


# ============================================================
# 描述符安全 + 防重入（🔴 Gemini A.1 / B.2 修复）
# ============================================================


def test_classmethod_patch_preserves_type(collector):
    """classmethod 被 patch 后仍是 classmethod，cls 参数正确传递"""

    class Fake:
        @classmethod
        def my_cls_method(cls, x: int) -> str:
            return f"{cls.__name__}:{x}"

    _safe_patch_method(
        Fake,
        "my_cls_method",
        "test.cm",
        _make_sync_wrapper,
        lambda a, k, r: {"r": r},
    )
    try:
        # classmethod 类型仍保留
        assert isinstance(Fake.__dict__["my_cls_method"], classmethod)
        with init_run("r_cm"):
            assert Fake.my_cls_method(7) == "Fake:7"
    finally:
        _force_restore(Fake, "my_cls_method")


def test_staticmethod_patch_preserves_type(collector):
    class Fake:
        @staticmethod
        def my_static(x: int) -> int:
            return x + 1

    _safe_patch_method(
        Fake,
        "my_static",
        "test.sm",
        _make_sync_wrapper,
        lambda a, k, r: {"r": r},
    )
    try:
        assert isinstance(Fake.__dict__["my_static"], staticmethod)
        with init_run("r_sm"):
            assert Fake.my_static(9) == 10
    finally:
        _force_restore(Fake, "my_static")


def test_double_patch_prevented_by_marker(collector):
    """第二次 patch 同一方法应直接跳过（PATCH_MARKER 防 wrapped→wrapped 死循环）"""

    class Fake:
        def method(self, x: int) -> int:
            return x

    _safe_patch_method(
        Fake, "method", "test.first", _make_sync_wrapper, lambda a, k, r: {}
    )
    first_wrapped = Fake.method
    # 第二次 patch 应识别 PATCH_MARKER 并跳过
    _safe_patch_method(
        Fake, "method", "test.second", _make_sync_wrapper, lambda a, k, r: {}
    )
    second_wrapped = Fake.method
    # 应当是同一对象（未二次包装）
    assert first_wrapped is second_wrapped
    assert getattr(Fake.method, PATCH_MARKER, False) is True
    _force_restore(Fake, "method")


# ============================================================
# _wrap_async 行为
# ============================================================


def test_async_patch_basic(collector):
    class Fake:
        async def method(self, x: int) -> int:
            await asyncio.sleep(0)
            return x * 3

    _safe_patch_method(
        Fake,
        "method",
        "test.async",
        _make_async_wrapper,
        lambda a, k, r: {"result": r},
    )
    try:
        inst = Fake()
        with init_run("r_async"):
            assert asyncio.run(inst.method(4)) == 12
        names = [e.name for e in collector.events]
        assert names.count("test.async") == 2
    finally:
        _force_restore(Fake, "method")


# ============================================================
# install / uninstall
# ============================================================


def test_install_uninstall_idempotent():
    install()
    install()  # 二次调用不报错
    assert is_installed() is True
    methods_before = sorted(installed_methods())
    uninstall()
    assert is_installed() is False
    # 二次 uninstall 不报错
    uninstall()
    # 再次 install 应该一样
    install()
    methods_after = sorted(installed_methods())
    assert methods_before == methods_after
    uninstall()


# ============================================================
# Attr extractor 边界
# ============================================================


def test_extract_chat_attrs_full():
    # 模拟 openai 1.x ChatCompletion
    result = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=45, total_tokens=165),
        choices=[SimpleNamespace(message=SimpleNamespace(content="布偶猫"))],
    )
    kwargs = {
        "model": "gemini-3.5-flash",
        "messages": [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "团子是什么品种？"},
        ],
    }
    attrs = _extract_chat_attrs((), kwargs, result)
    assert attrs["model"] == "gemini-3.5-flash"
    assert attrs["prompt_tokens"] == 120
    assert attrs["completion_tokens"] == 45
    assert attrs["total_tokens"] == 165
    assert attrs["messages_count"] == 2
    assert "团子" in attrs["prompt_preview"]
    assert attrs["completion_preview"] == "布偶猫"


def test_extract_chat_attrs_missing_usage():
    result = SimpleNamespace(usage=None, choices=[])
    attrs = _extract_chat_attrs((), {"model": "x"}, result)
    assert attrs["model"] == "x"
    assert "prompt_tokens" not in attrs


def test_extract_chat_attrs_empty():
    # 极端情况：什么都没传
    attrs = _extract_chat_attrs((), {}, None)
    assert attrs == {}


def test_extract_embedding_attrs_list_input():
    result = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * 512)])
    kwargs = {"model": "bge", "input": ["猫", "狗"]}
    attrs = _extract_embedding_attrs((), kwargs, result)
    assert attrs["model"] == "bge"
    assert attrs["batch_size"] == 2
    assert attrs["dim"] == 512
    assert attrs["first_text_preview"] == "猫"


def test_extract_embedding_attrs_str_input():
    result = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])
    kwargs = {"input": "猫咪"}
    attrs = _extract_embedding_attrs((), kwargs, result)
    assert attrs["batch_size"] == 1
    assert attrs["dim"] == 2
    assert attrs["first_text_preview"] == "猫咪"


def test_extract_st_attrs_numpy_like():
    # 模拟 numpy ndarray
    result = SimpleNamespace(shape=(3, 512))
    args = (object(), ["a", "b", "c"])  # args[0] = self
    attrs = _extract_st_attrs(args, {}, result)
    assert attrs["batch_size"] == 3
    assert attrs["shape"] == [3, 512]
    assert attrs["dim"] == 512
    assert attrs["first_text_preview"] == "a"


def test_extract_st_attrs_str_input():
    result = SimpleNamespace(shape=(512,))
    args = (object(), "hello")
    attrs = _extract_st_attrs(args, {}, result)
    assert attrs["batch_size"] == 1
    assert attrs["first_text_preview"] == "hello"


def test_extract_st_attrs_kwarg_form():
    result = SimpleNamespace(shape=(2, 512))
    args = (object(),)
    kwargs = {"sentences": ["x", "y"]}
    attrs = _extract_st_attrs(args, kwargs, result)
    assert attrs["batch_size"] == 2


def test_extract_st_attrs_single_text_1d_list(collector):
    """🟡 Gemini C.1：单条文本 encode 返回 list[float]（1D）时，
    len(result) = dim，不应被误认为 count=dim"""
    fake_1d_list = [0.0] * 512  # 单条 encode 返回的 1D list
    args = (object(), "团子是布偶猫")
    attrs = _extract_st_attrs(args, {}, fake_1d_list)
    assert attrs["batch_size"] == 1
    assert attrs["dim"] == 512  # 不是 batch_size=512
    assert "count" not in attrs  # 单条情况不写 count


def test_extract_chat_attrs_dict_response(collector):
    """🔵 Gemini C.2：OneAPI / Mock 返回原生 dict 而非 Pydantic 对象"""
    # 模拟 OneAPI 风格 dict response
    dict_result = {
        "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
        "choices": [{"message": {"content": "布偶猫"}}],
    }
    attrs = _extract_chat_attrs((), {"model": "gemini"}, dict_result)
    assert attrs["prompt_tokens"] == 50
    assert attrs["completion_tokens"] == 25
    assert attrs["total_tokens"] == 75
    assert attrs["completion_preview"] == "布偶猫"


# ============================================================
# ThreadPoolExecutor ContextVars 传播（🔴 Gemini B.1）
# ============================================================


def test_threadpool_executor_context_propagation(collector):
    """install() 后，ThreadPoolExecutor 子线程的 is_tracing() 应 True"""
    from concurrent.futures import ThreadPoolExecutor

    from kidsbench.trace.span import is_tracing as _is_tracing

    install()
    try:
        with init_run("r_thread"):
            # 子线程内调 is_tracing 必须为 True（contextvars 跨线程传播）
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_is_tracing)
                result = future.result(timeout=2)
                assert result is True
    finally:
        uninstall()


def test_threadpool_executor_not_traced_outside(collector):
    """install() 但不在 init_run 内时，子线程 is_tracing() False"""
    from concurrent.futures import ThreadPoolExecutor

    from kidsbench.trace.span import is_tracing as _is_tracing

    install()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_is_tracing)
            assert future.result(timeout=2) is False
    finally:
        uninstall()


# ============================================================
# 内部辅助
# ============================================================


def _force_restore(cls: type, method_name: str) -> None:
    """从 _originals 找回原方法并还原 + 清条目，给测试 isolation 用。"""
    from kidsbench.trace.llm_hook import _originals  # noqa: PLC2701

    key = (cls, method_name)
    if key in _originals:
        setattr(cls, method_name, _originals[key])
        del _originals[key]
