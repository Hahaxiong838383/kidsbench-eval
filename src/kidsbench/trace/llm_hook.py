"""Monkey-patch 第三方库给内部 LLM / embedding 调用打 span（B1.1 第二刀）。

为什么需要：
- harness 直接调的方法（adapter.write/read 等）已用 TracedAdapter 装好
- 但 mem0 / memoryos SDK 内部还会自己调 LLM 抽 facts、调 embedding
  这些是黑盒，无法用装饰器进入
- 解决：运行时替换第三方库的方法（monkey-patch），让 openai SDK / sentence_transformers
  调用都自动产生 span

支持的库：
- openai >= 1.x：sync + async Completions / Embeddings
- sentence_transformers.SentenceTransformer.encode

设计原则（与 trace 模块一致）：
- install() / uninstall() 全局 idempotent + 线程安全
- patch 失败不阻断业务（try/except + log debug）
- 不 init_run 时 patched 方法直接透传（双层 short-circuit：wrapped 内部 + @span 内部）
- uninstall 完全还原，不留痕迹

Gemini 3.5-flash high reasoning 评审加固（B1.1 第二刀 round 2）：
- 🔴 描述符安全（classmethod/staticmethod 不能裸 setattr）
- 🔴 多线程 ContextVars 传播（ThreadPoolExecutor.submit 也要 patch）
- 🔴 防重入 PATCH_MARKER（防 wrapped 调 wrapped 死循环爆栈）
- 🟡 装饰器顺序（@span_decorator 在外，@functools.wraps 在内）
- 🟡 SentenceTransformer 单条 vs Batch 维度修正
- 🔵 dict 类型 response 兼容（OneAPI / Mock）
- 🔵 wrapped 第一行 is_tracing() 短路（极致 passthrough）
"""

from __future__ import annotations

import contextvars
import functools
import logging
import threading
from typing import Any, Callable

from .span import is_tracing, preview, span as span_decorator, span_attr

_log = logging.getLogger(__name__)

# 全局锁与状态保护
_patch_lock = threading.Lock()
_originals: dict[tuple[type, str], Any] = {}
_installed = False

# 标记已被本 hook patch 的属性，防 install 二次包装导致 wrapped→wrapped 死循环
PATCH_MARKER = "__kidsbench_patched__"


# ============================================================
# 公共 API
# ============================================================


def install() -> None:
    """安装所有可用的 hook。线程安全，多次调用幂等。

    建议在 harness 启动时调一次（--trace 时），跑完调 uninstall。
    """
    global _installed
    with _patch_lock:
        if _installed:
            return
        _patch_thread_pool_executor()
        _patch_openai_v1()
        _patch_sentence_transformers()
        _installed = True
        _log.debug("trace.llm_hook installed: %d methods", len(_originals))


def uninstall() -> None:
    """恢复所有原方法。线程安全，完全还原。"""
    global _installed
    with _patch_lock:
        if not _installed:
            return
        for (cls, method_name), original_descriptor in list(_originals.items()):
            try:
                # original 已经是原始 descriptor（含 classmethod/staticmethod wrapper），
                # 直接 setattr 恢复
                setattr(cls, method_name, original_descriptor)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "uninstall %s.%s failed: %s", cls.__name__, method_name, exc
                )
        _originals.clear()
        _installed = False
        _log.debug("trace.llm_hook uninstalled")


def is_installed() -> bool:
    return _installed


def installed_methods() -> list[str]:
    """返回当前已 patch 的方法名（debug 用）。"""
    return [f"{cls.__name__}.{name}" for (cls, name) in _originals]


# ============================================================
# 多线程 ContextVars 传播（🔴 Gemini B.1）
# ============================================================


def _patch_thread_pool_executor() -> None:
    """Patch ThreadPoolExecutor.submit 让 trace contextvars 自动跨线程传播。

    Python 默认 contextvars 不跨线程。harness 未来若并行评测，
    子线程的 is_tracing() 会返回 False，所有 LLM 调用变成 passthrough，
    trace 数据断链。这里 wrap submit 让每个 task 在 copy_context 内跑。
    """
    from concurrent.futures import ThreadPoolExecutor

    _safe_patch_callable(
        cls=ThreadPoolExecutor,
        method_name="submit",
        wrapper_factory=_make_submit_wrapper,
    )


def _make_submit_wrapper(original: Callable) -> Callable:
    @functools.wraps(original)
    def patched_submit(self: Any, fn: Callable, /, *args: Any, **kwargs: Any) -> Any:
        ctx = contextvars.copy_context()
        return original(self, ctx.run, fn, *args, **kwargs)

    return patched_submit


# ============================================================
# OpenAI / sentence_transformers patch（🔴 Gemini A.1 描述符安全）
# ============================================================


def _patch_openai_v1() -> None:
    """OpenAI Python SDK 1.x（mem0 / memoryos / graphiti 内部都用）。"""
    try:
        import openai.resources.chat.completions as oai_chat
        import openai.resources.embeddings as oai_emb
    except ImportError:
        _log.debug("openai SDK 未装，跳过 patch")
        return

    if hasattr(oai_chat, "Completions"):
        _safe_patch_method(
            oai_chat.Completions,
            "create",
            "llm.openai.chat",
            _make_sync_wrapper,
            _extract_chat_attrs,
        )
    if hasattr(oai_chat, "AsyncCompletions"):
        _safe_patch_method(
            oai_chat.AsyncCompletions,
            "create",
            "llm.openai.chat",
            _make_async_wrapper,
            _extract_chat_attrs,
        )
    if hasattr(oai_emb, "Embeddings"):
        _safe_patch_method(
            oai_emb.Embeddings,
            "create",
            "embedding.openai",
            _make_sync_wrapper,
            _extract_embedding_attrs,
        )
    if hasattr(oai_emb, "AsyncEmbeddings"):
        _safe_patch_method(
            oai_emb.AsyncEmbeddings,
            "create",
            "embedding.openai",
            _make_async_wrapper,
            _extract_embedding_attrs,
        )


def _patch_sentence_transformers() -> None:
    """sentence-transformers 库（mem0 / memoryos / graphiti 本地 embedding 用）。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        _log.debug("sentence_transformers 未装，跳过 patch")
        return

    _safe_patch_method(
        SentenceTransformer,
        "encode",
        "embedding.sentence_transformers",
        _make_sync_wrapper,
        _extract_st_attrs,
    )


# ============================================================
# 通用 safe patch（🔴 Gemini A.1 + B.2 加固）
# ============================================================


def _resolve_original(raw_descriptor: Any, cls: type, method_name: str) -> Any:
    """从 raw_descriptor 解出真正可调用的 function。

    - classmethod/staticmethod：拿 .__func__（unbound function，避免外层重包 classmethod
      时双重 bind cls 导致 TypeError: 3 args given）
    - 普通 instance method：getattr(cls, name) 返回 function（Py3 unbound）

    返回的 callable 可直接调 `original(*args, **kwargs)` 透传所有参数。
    """
    if isinstance(raw_descriptor, (classmethod, staticmethod)):
        return raw_descriptor.__func__
    return getattr(cls, method_name)


def _safe_patch_callable(
    *,
    cls: type,
    method_name: str,
    wrapper_factory: Callable[[Callable], Callable],
) -> None:
    """简化版 safe_patch：只关心 callable，无 attr extractor。

    用于 ThreadPoolExecutor.submit 这类不需要打 span 的方法。
    """
    key = (cls, method_name)
    if key in _originals:
        return
    raw_descriptor = cls.__dict__.get(method_name)
    if raw_descriptor is None:
        return

    original = _resolve_original(raw_descriptor, cls, method_name)
    if getattr(original, PATCH_MARKER, False):
        _log.debug("%s.%s 已 patch，跳过", cls.__name__, method_name)
        return

    _originals[key] = raw_descriptor

    wrapped = wrapper_factory(original)
    try:
        setattr(wrapped, PATCH_MARKER, True)
    except (AttributeError, TypeError):
        pass

    if isinstance(raw_descriptor, classmethod):
        wrapped = classmethod(wrapped)
    elif isinstance(raw_descriptor, staticmethod):
        wrapped = staticmethod(wrapped)

    setattr(cls, method_name, wrapped)


def _safe_patch_method(
    cls: type,
    method_name: str,
    span_name: str,
    wrapper_factory: Callable[[Any, str, Callable], Callable],
    extract: Callable,
) -> None:
    """带 attr extractor 的安全 patch。

    防御：
    - cls.__dict__.get(name) 拿原 descriptor（保留 classmethod/staticmethod 类型）
    - _resolve_original 解出 unbound function（classmethod 不会双重 bind）
    - PATCH_MARKER 检测是否已被本 hook 包过（防 wrapped→wrapped 死循环）
    """
    key = (cls, method_name)
    if key in _originals:
        return
    raw_descriptor = cls.__dict__.get(method_name)
    if raw_descriptor is None:
        return

    original = _resolve_original(raw_descriptor, cls, method_name)
    if getattr(original, PATCH_MARKER, False):
        _log.debug("%s.%s 已 patch，跳过", cls.__name__, method_name)
        return

    _originals[key] = raw_descriptor

    wrapped = wrapper_factory(original, span_name, extract)
    try:
        setattr(wrapped, PATCH_MARKER, True)
    except (AttributeError, TypeError):
        pass

    if isinstance(raw_descriptor, classmethod):
        wrapped = classmethod(wrapped)
    elif isinstance(raw_descriptor, staticmethod):
        wrapped = staticmethod(wrapped)

    setattr(cls, method_name, wrapped)


# ============================================================
# 包装器生成器（🟡 Gemini A.2 装饰器顺序修复）
# ============================================================


def _make_sync_wrapper(
    original: Any, span_name: str, extract: Callable
) -> Callable:
    """生成 Sync 包装器。

    装饰器顺序：@span_decorator 在外 + @functools.wraps 在内
    （内层函数签名通过 wraps 复制自 original 给可能的 inspect 用户）
    """

    @span_decorator(span_name)
    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        # 极致 passthrough（🔵 Gemini D.1）：is_tracing False 直接调原方法
        if not is_tracing():
            return original(*args, **kwargs)

        result = original(*args, **kwargs)
        try:
            attrs = extract(args, kwargs, result)
            if attrs:
                span_attr(**attrs)
        except Exception as exc:  # noqa: BLE001
            _log.debug("extract attrs failed for %s: %s", span_name, exc)
        return result

    return wrapped


def _make_async_wrapper(
    original: Any, span_name: str, extract: Callable
) -> Callable:
    """生成 Async 包装器。同 sync 版本但 await。"""

    @span_decorator(span_name)
    @functools.wraps(original)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not is_tracing():
            return await original(*args, **kwargs)

        result = await original(*args, **kwargs)
        try:
            attrs = extract(args, kwargs, result)
            if attrs:
                span_attr(**attrs)
        except Exception as exc:  # noqa: BLE001
            _log.debug("extract attrs failed for %s: %s", span_name, exc)
        return result

    return wrapped


# ============================================================
# Attr 抽取器（🔵 Gemini C.2 dict 兼容 + 🟡 C.1 ST 维度修正）
# ============================================================


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """安全取属性，兼容 Pydantic 对象 / 原生 dict。

    用于 OneAPI / Mock 等返回 dict 而非 ChatCompletion 对象的场景。
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_chat_attrs(args: tuple, kwargs: dict, result: Any) -> dict:
    """openai chat.completions.create 调用提取。"""
    out: dict[str, Any] = {}

    if "model" in kwargs:
        out["model"] = kwargs["model"]

    messages = kwargs.get("messages", [])
    if isinstance(messages, (list, tuple)) and messages:
        out["messages_count"] = len(messages)
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    out["prompt_preview"] = preview(content, 200)
                break

    try:
        usage = _safe_get(result, "usage")
        if usage is not None:
            pt = _safe_get(usage, "prompt_tokens")
            ct = _safe_get(usage, "completion_tokens")
            tt = _safe_get(usage, "total_tokens")
            if pt is not None:
                out["prompt_tokens"] = pt
            if ct is not None:
                out["completion_tokens"] = ct
            if tt is not None:
                out["total_tokens"] = tt

        choices = _safe_get(result, "choices", []) or []
        if choices:
            msg = _safe_get(choices[0], "message")
            if msg is not None:
                content = _safe_get(msg, "content", "") or ""
                if content:
                    out["completion_preview"] = preview(str(content), 200)
    except Exception:  # noqa: BLE001
        pass
    return out


def _extract_embedding_attrs(args: tuple, kwargs: dict, result: Any) -> dict:
    """openai embeddings.create 调用提取。"""
    out: dict[str, Any] = {}

    if "model" in kwargs:
        out["model"] = kwargs["model"]

    inp = kwargs.get("input")
    if isinstance(inp, str):
        out["batch_size"] = 1
        out["first_text_preview"] = preview(inp, 150)
    elif inp is not None:
        try:
            n = len(inp)
            out["batch_size"] = n
            if n > 0 and isinstance(inp[0], str):
                out["first_text_preview"] = preview(inp[0], 150)
        except (TypeError, IndexError):
            pass

    try:
        data = _safe_get(result, "data", []) or []
        if data:
            emb = _safe_get(data[0], "embedding")
            if emb is not None:
                try:
                    out["dim"] = len(emb)
                except TypeError:
                    pass
    except Exception:  # noqa: BLE001
        pass
    return out


def _extract_st_attrs(args: tuple, kwargs: dict, result: Any) -> dict:
    """sentence_transformers.SentenceTransformer.encode 调用提取。

    🟡 Gemini C.1 修复：区分单条文本（result 是 1D，len=dim）vs Batch
    （result 是 2D 或 list[ndarray]，len=batch_size）。

    instance method: args[0] = self (SentenceTransformer 实例)
    args[1] 或 kwargs['sentences'] = texts (str | list[str])
    """
    out: dict[str, Any] = {}

    texts: Any = None
    if len(args) >= 2:
        texts = args[1]
    if texts is None:
        texts = kwargs.get("sentences", kwargs.get("texts"))

    is_single = isinstance(texts, str)

    if is_single:
        out["batch_size"] = 1
        out["first_text_preview"] = preview(texts, 150)
    elif texts is not None:
        try:
            n = len(texts)
            out["batch_size"] = n
            if n > 0:
                first = texts[0]
                if isinstance(first, str):
                    out["first_text_preview"] = preview(first, 150)
        except (TypeError, IndexError):
            pass

    try:
        if hasattr(result, "shape"):
            shape = list(result.shape)
            out["shape"] = shape
            if shape:
                out["dim"] = shape[-1]
        elif hasattr(result, "__len__"):
            if is_single:
                # 单条 + 返回 list[float] → len = dim
                out["dim"] = len(result)
            else:
                # Batch + 返回 list[list[float]] → len = batch
                n = len(result)
                out["count"] = n
                if n > 0:
                    first_vec = result[0]
                    if hasattr(first_vec, "__len__"):
                        out["dim"] = len(first_vec)
    except Exception:  # noqa: BLE001
        pass
    return out
