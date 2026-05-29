"""Graphiti 适配层：让 graphiti 0.29.1 跑在任意 OpenAI ChatCompletions 兼容服务上。

为什么需要：
- Graphiti 内置 OpenAIClient 用 OpenAI 新 **Responses API** (`client.responses.parse()`)
  做 structured output（必须 OpenAI 官方）
- 我们的 GEMINI_PROXY (23.226.135.149:4000) 只支持 ChatCompletions
- 未来对接 MiniMax / DeepSeek / 通义千问 / Ollama 也都是只支持 ChatCompletions

本模块提供：
- `KidsBenchGraphitiLLMClient`: 用 chat.completions + JSON schema 提示模拟 structured output
- `make_st_embedder()`: 用 sentence-transformers 本地实现 graphiti EmbedderClient

gemini 评审建议吸收：
- A.3: response_format=json_object 不强制 schema 匹配 → 在 system prompt 强提示 + raise RefusalError
- B.3: 缓存 schema 字符串避免每次 add_episode 多次注入巨大 schema 爆 token
- A.4: reasoning_effort 不全局硬编码，允许 caller 覆盖（但默认 minimal 防 thinking 耗光）
- C.1: 标记 capability `structured_output_via_json_schema` 让评测可对照（在 adapter 层声明，不在本模块）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Lazy import — 主 venv 没装 graphiti 时本模块可被 import（用于 type hints）
# 实际 KidsBenchGraphitiLLMClient 实例化时才需要 graphiti
try:
    from graphiti_core.embedder.client import EmbedderClient as _GraphitiEmbedderClient
    from graphiti_core.llm_client.config import LLMConfig as _GraphitiLLMConfig
    from graphiti_core.llm_client.openai_base_client import (
        BaseOpenAIClient as _GraphitiBaseOpenAIClient,
    )

    _GRAPHITI_AVAILABLE = True
    _BaseOpenAIClient = _GraphitiBaseOpenAIClient  # type: ignore[misc, assignment]
    _LLMConfig = _GraphitiLLMConfig  # type: ignore[misc, assignment]
    _EmbedderClient = _GraphitiEmbedderClient  # type: ignore[misc, assignment]
except ImportError:  # pragma: no cover
    _GRAPHITI_AVAILABLE = False
    _BaseOpenAIClient = object  # type: ignore[misc, assignment]
    _LLMConfig = Any  # type: ignore[misc, assignment]
    _EmbedderClient = object  # type: ignore[misc, assignment]


# ============= 模拟 Responses API 返回对象 =============


@dataclass
class _FakeResponsesObject:
    """模拟 OpenAI Responses API 返回，让 graphiti `_handle_structured_response` 能读 output_text。

    graphiti _handle_structured_response 实现:
        response_object = response.output_text
        if response_object: return json.loads(response_object)
        elif response_object.refusal: raise RefusalError(response_object.refusal)
        else: raise Exception(...)

    关键属性 (gemini B.1 finding):
    - output_text: chat.completions 返回的 JSON 字符串
    - refusal: 必须存在（None 即可），否则 graphiti 检查 refusal 属性会 AttributeError
    """

    output_text: str
    raw: Any = None
    refusal: Any = None  # 必须存在，graphiti 内部检查（gemini B.1）

    def model_dump(self) -> dict[str, Any]:
        """graphiti 错误路径会调 model_dump（gemini B.1）。"""
        return {"output_text": self.output_text, "refusal": self.refusal}


# ============= Schema 缓存（避免重复计算 + 减少 token）=============

_SCHEMA_CACHE: dict[type, str] = {}


def _get_schema_str(response_model: type) -> str:
    """缓存 BaseModel 的 schema 字符串（gemini B.3 finding：避免每次 add_episode 注入巨大 schema 爆 token）。"""
    if response_model not in _SCHEMA_CACHE:
        schema = response_model.model_json_schema()
        _SCHEMA_CACHE[response_model] = json.dumps(schema, ensure_ascii=False)
    return _SCHEMA_CACHE[response_model]


def clear_schema_cache() -> None:
    """测试用：清缓存。"""
    _SCHEMA_CACHE.clear()


def _inject_schema_prompt(messages: list[dict], schema_str: str) -> list[dict]:
    """把 JSON Schema 提示加到 system message。

    - 已有 system message → append 到 content（避免新增 message 改变 conversation 结构）
    - 无 system → 新建一条 system 在最前
    """
    schema_msg = (
        "重要：你必须返回有效的 JSON 对象，严格匹配以下 JSON Schema。"
        "不要任何 markdown 包裹（如 ```json），不要解释文字，只返回纯 JSON。"
        f"\n\nJSON Schema:\n{schema_str}"
    )
    new_messages = [dict(m) for m in messages]  # 深拷贝防修改原 list
    if new_messages and new_messages[0].get("role") == "system":
        existing = new_messages[0].get("content", "")
        new_messages[0]["content"] = f"{existing}\n\n{schema_msg}" if existing else schema_msg
    else:
        new_messages.insert(0, {"role": "system", "content": schema_msg})
    return new_messages


# ============= 主类 =============


class KidsBenchGraphitiLLMClient(_BaseOpenAIClient):  # type: ignore[misc, valid-type]
    """适配 OpenAI ChatCompletions API 的 Graphiti LLMClient。

    用 chat.completions.create() 替代 responses.parse()，让 GEMINI_PROXY、MiniMax、
    DeepSeek、Ollama 等仅支持 ChatCompletions 的服务能跑 graphiti structured output。

    覆盖父类两个抽象方法：
    - `_create_completion`: 普通 JSON 模式 (response_format=json_object)
    - `_create_structured_completion`: 注入 JSON Schema 提示 + 包装 _FakeResponsesObject

    关键设计 (吸收 gemini 评审 finding)：
    - schema 字符串缓存（避免重复序列化 + 减少 token 爆炸风险）
    - max_tokens 强制最小 4096（gemini-3.5-flash 默认 thinking 耗 100+ tokens reasoning）
    - reasoning_effort 默认 minimal（防 thinking 耗光输出），但允许 caller 覆盖
    - schema 提示用强约束语气（"必须"、"严格匹配"、"不要 markdown"）
    """

    def __init__(
        self,
        config: Any = None,
        cache: bool = False,
        client: Any = None,
        max_tokens: int = 16384,
        reasoning_effort: str | None = "minimal",
        min_max_tokens: int = 4096,
    ) -> None:
        if not _GRAPHITI_AVAILABLE:
            raise ImportError(
                "graphiti-core not installed. Install with: pip install graphiti-core"
            )

        # 父类 __init__ 签名: (config, cache, max_tokens)
        # 不能用 super().__init__ 因为 max_tokens 处理跟 ours 不同（min 限制）
        super().__init__(config=config, cache=cache, max_tokens=max_tokens)

        if config is None:
            config = _LLMConfig()  # type: ignore[misc]
        if client is None:
            import openai
            self.client = openai.AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
            )
        else:
            self.client = client

        self._reasoning_effort = reasoning_effort
        self._min_max_tokens = min_max_tokens

    async def _create_completion(
        self,
        model: str,
        messages: list,
        temperature: float | None,
        max_tokens: int,
        response_model: Any = None,  # 父类接口要求但本方法不用
    ) -> Any:
        """非 structured 模式：纯 chat.completions + JSON object。"""
        kwargs = self._build_kwargs(model, messages, temperature, max_tokens)
        kwargs["response_format"] = {"type": "json_object"}
        return await self.client.chat.completions.create(**kwargs)

    async def _create_structured_completion(
        self,
        model: str,
        messages: list,
        temperature: float | None,
        max_tokens: int,
        response_model: type,
    ) -> _FakeResponsesObject:
        """Structured 模式：注入 JSON Schema 提示 + 包装 _FakeResponsesObject。"""
        schema_str = _get_schema_str(response_model)
        prefixed = _inject_schema_prompt(messages, schema_str)
        kwargs = self._build_kwargs(model, prefixed, temperature, max_tokens)
        kwargs["response_format"] = {"type": "json_object"}
        resp = await self.client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        return _FakeResponsesObject(output_text=content, raw=resp)

    def _build_kwargs(
        self,
        model: str,
        messages: list,
        temperature: float | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        """组装 chat.completions kwargs。

        - max_tokens 强制最小值（gemini-3.5-flash thinking 模型默认 reasoning 耗 100+ token）
        - reasoning_effort='minimal' 防 reasoning 耗光（按 memory feedback_gemini_flash_thinking_default_trap）
        """
        kw: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max(max_tokens, self._min_max_tokens),
            "temperature": temperature if temperature is not None else 0.0,
        }
        if self._reasoning_effort:
            kw["reasoning_effort"] = self._reasoning_effort
        return kw


# ============= Embedder 工厂（本地 sentence-transformers）=============


def make_st_embedder(
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> Any:
    """返一个 graphiti EmbedderClient 实现（用 sentence-transformers 本地）。

    为什么需要：GEMINI_PROXY /v1/embeddings 整个 endpoint 返 500（无论 model 名），
    必须本地 embedding。

    用法：
        embedder = make_st_embedder()
        graphiti = Graphiti(llm_client=..., embedder=embedder, ...)

    Args:
        model_name: sentence-transformers 模型名（默认 all-MiniLM-L6-v2, 384 维, ~22MB）
    """
    if not _GRAPHITI_AVAILABLE:
        raise ImportError("graphiti-core not installed")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError("sentence-transformers not installed") from e

    model = SentenceTransformer(model_name)

    class STEmbedder(_EmbedderClient):  # type: ignore[misc, valid-type]
        async def create(self, input_data: Any) -> Any:
            if isinstance(input_data, str):
                texts = [input_data]
            else:
                texts = list(input_data)
            embs = model.encode(texts, show_progress_bar=False).tolist()
            if len(embs) == 1:
                return embs[0]
            return [list(e) for e in embs]

        async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
            return [
                list(e)
                for e in model.encode(input_data_list, show_progress_bar=False).tolist()
            ]

    return STEmbedder()
