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
    # 中文 K12 场景实测 bge-small-zh-v1.5 区分度 0.467（vs all-MiniLM 0.264）
    model_name: str = "BAAI/bge-small-zh-v1.5",
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


# ============= 真实 Graphiti async 实例 → adapter Mock 接口 wrapper =============


class _RealGraphitiWrapper:
    """适配真实 graphiti async 实例到 GraphitiAdapter 期望的 Mock 接口。

    真实 graphiti 0.18.9 API（跟 codex 假设差异巨大）:
    - add_episode(name, episode_body, source_description, reference_time, source, group_id, ...)
      —— **没有 metadata 参数**
    - search(query, center_node_uuid, group_ids, num_results, search_filter)
    - remove_episode(episode_uuid) —— **没有 delete_session（codex 假设错的）**

    adapter Mock 假设的接口:
    - add_episode(name, episode_body, metadata) —— metadata dict 含 turn_id/role/user_id/ts
    - search(query, search_config) —— search_config dict
    - delete_session(name) —— 按 session_name 删

    Wrapper 工作：
    - add_episode: 从 metadata 提取 group_id (user_id) + reference_time，turn_id/role 拼到
      source_description，让 graphiti LLM 抽取时看到，episode_uuid 缓存在 session_name → []
    - search: search_config dict 拆 group_ids / num_results
    - delete_session: 查 episode_uuids by session_name 挨个 remove_episode（无原生按 name 删）
    """

    def __init__(self, graphiti: Any) -> None:
        import asyncio
        import threading
        from datetime import datetime, timezone

        from graphiti_core.nodes import EpisodeType

        self._g = graphiti
        self._EpisodeType = EpisodeType
        self._datetime = datetime
        self._timezone = timezone
        # session_name → list[episode_uuid]，adapter clear 时用
        self._episode_uuids_by_session: dict[str, list[str]] = {}

        # 关键：持久 background event loop（独立 thread）
        # 因为 graphiti 的 FalkorDB driver 用 redis.asyncio 持久化连接，
        # 连接绑定第一个建立它的 loop。如果 adapter._run 每次 asyncio.run
        # 新建 loop，连接复用时会触发 'Event loop is closed' RuntimeError。
        # 通过持久 loop 让所有 async 调用走同一 loop，连接稳定。
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="kidsbench-graphiti-loop"
        )
        self._thread.start()

    def _run_async(self, coro: Any) -> Any:
        """同步等 coroutine 在 background loop 完成。"""
        import asyncio

        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    def add_episode(
        self, name: str, episode_body: str, metadata: dict | None = None
    ) -> dict[str, Any]:
        """Sync 接口；内部走 background loop 跑真实 async graphiti.add_episode。

        返 sync result 让 adapter._run 直接 return（不会再 asyncio.run 一次）。
        """
        return self._run_async(self._add_episode_async(name, episode_body, metadata))

    async def _add_episode_async(
        self, name: str, episode_body: str, metadata: dict | None = None
    ) -> dict[str, Any]:
        metadata = metadata or {}
        # group_id 优先从 metadata 取 user_id，否则从 session_name 解析（u_{user_id}_{session_id}）
        group_id = (
            metadata.get("user_id")
            or metadata.get("group_id")
            or self._parse_user_id_from_session(name)
            or "default"
        )

        # reference_time 从 metadata.ts 或 current_time（float 时间戳）取
        ts = metadata.get("ts") or metadata.get("current_time")
        if isinstance(ts, (int, float)):
            ref_time = self._datetime.fromtimestamp(float(ts), tz=self._timezone.utc)
        else:
            ref_time = self._datetime.now(self._timezone.utc)

        # 把 metadata 关键字段拼到 source_description（让 graphiti LLM 抽取时看见）
        turn_id = metadata.get("turn_id", "")
        role = metadata.get("role", "user")
        source_desc = f"K12 chat[turn_id={turn_id}, role={role}]"

        result = await self._g.add_episode(
            name=name,
            episode_body=episode_body,
            source_description=source_desc,
            reference_time=ref_time,
            source=self._EpisodeType.message,
            group_id=str(group_id),
        )

        # AddEpisodeResults: episode/nodes/edges
        ep_uuid = self._safe_uuid(getattr(result, "episode", None))
        if ep_uuid:
            self._episode_uuids_by_session.setdefault(name, []).append(ep_uuid)

        nodes = getattr(result, "nodes", None) or []
        edges = getattr(result, "edges", None) or []
        node_uuids = [self._safe_uuid(n) for n in nodes]
        edge_uuids = [self._safe_uuid(e) for e in edges]

        return {
            "entity_id": ep_uuid or "",
            "uuid": ep_uuid or "",
            "entity_ids": [u for u in node_uuids if u],
            "relation_ids": [u for u in edge_uuids if u],
            # 让 adapter _record_belongs_to_user 检查能命中
            "metadata": {
                "session_name": name,
                "turn_id": turn_id,
                "user_id": group_id,
            },
        }

    def search(self, query: str, search_config: Any = None) -> dict[str, Any]:
        """Sync 接口；内部走 background loop。"""
        return self._run_async(self._search_async(query, search_config))

    async def _search_async(
        self, query: str, search_config: Any = None
    ) -> dict[str, Any]:
        # search_config 可能是 dict 或 string (graphiti recipe name) 或 None
        # adapter 的 _default_search_config 默认是 'COMBINED_HYBRID_SEARCH_RRF' 字符串
        if isinstance(search_config, dict):
            group_ids = search_config.get("group_ids")
            num_results = int(search_config.get("num_results", 10))
        else:
            # string recipe / None: 无 group_ids 限制（adapter sidecar 后续会过滤）
            group_ids = None
            num_results = 10

        edges = await self._g.search(
            query=query,
            group_ids=group_ids,
            num_results=num_results,
        )

        items: list[dict[str, Any]] = []
        for e in edges:
            uuid = self._safe_uuid(e) or ""
            fact = str(getattr(e, "fact", "") or "")
            name = str(getattr(e, "name", "") or "")
            text = fact or name or uuid
            score_attr = getattr(e, "score", None)
            try:
                score = float(score_attr) if score_attr is not None else 0.85
            except (TypeError, ValueError):
                score = 0.85
            items.append(
                {
                    "memory_id": uuid,
                    "uuid": uuid,
                    "name": fact or name,
                    "text": text,
                    "score": score,
                    "rrf_score": score,
                    "metadata": {},
                }
            )
        return {"items": items}

    def delete_session(self, name: str) -> dict[str, bool]:
        return self._run_async(self._delete_session_async(name))

    async def _delete_session_async(self, name: str) -> dict[str, bool]:
        ep_uuids = self._episode_uuids_by_session.pop(name, [])
        for uuid in ep_uuids:
            try:
                await self._g.remove_episode(uuid)
            except Exception:
                pass
        return {"ok": True}

    def flush_pending(self) -> dict[str, bool]:
        return {"ok": True}

    def get_stats(self, user_id: str) -> dict[str, int]:
        # graphiti 0.18.9 没暴露直接的 stats API；这里返回零（评测主要看 sidecar 统计）
        return {"node_count": 0, "edge_count": 0, "episode_count": 0}

    @staticmethod
    def _safe_uuid(obj: Any) -> str:
        if obj is None:
            return ""
        uuid = getattr(obj, "uuid", None)
        return str(uuid) if uuid else ""

    @staticmethod
    def _parse_user_id_from_session(session_name: str) -> str | None:
        """从 'u_{user_id}_{session_id}' 解析 user_id（GraphitiAdapter._session_name 格式）。"""
        if not session_name.startswith("u_"):
            return None
        parts = session_name.split("_")
        if len(parts) >= 3:
            return parts[1]
        return None


def make_real_graphiti_client_factory(
    *,
    api_key: str,
    base_url: str,
    model: str = "gemini-3.5-flash",
    falkor_host: str = "127.0.0.1",
    falkor_port: int = 16379,
    falkor_database: str = "kidsbench_eval",
    embedder_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    reasoning_effort: str = "minimal",
) -> Any:
    """工厂：返回一个 client_factory callable（适合 GraphitiAdapter config['client_factory']）。

    工厂被 adapter 调用时（接收 backend/uri/config kwargs）返回 _RealGraphitiWrapper 实例。

    用法（harness 中）：
        from kidsbench.middleware.graphiti_compat import make_real_graphiti_client_factory
        adapter = GraphitiAdapter(
            config={"client_factory": make_real_graphiti_client_factory(api_key=..., ...)},
            backend="falkordb", uri="redis://127.0.0.1:16379",
        )
    """
    if not _GRAPHITI_AVAILABLE:
        raise ImportError("graphiti-core not installed")

    def _factory(**_: Any) -> Any:
        from graphiti_core import Graphiti
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        config = _LLMConfig(  # type: ignore[misc]
            api_key=api_key,
            base_url=base_url,
            model=model,
            small_model=model,  # graphiti 默认 small=gpt-4.1-nano，需显式同 model
            temperature=0.0,
        )
        llm = KidsBenchGraphitiLLMClient(config=config, reasoning_effort=reasoning_effort)
        reranker = OpenAIRerankerClient(config=config)
        embedder = make_st_embedder(model_name=embedder_model)
        driver = FalkorDriver(
            host=falkor_host, port=falkor_port, database=falkor_database
        )
        graphiti = Graphiti(
            llm_client=llm,
            embedder=embedder,
            cross_encoder=reranker,
            graph_driver=driver,
        )
        wrapper = _RealGraphitiWrapper(graphiti)
        # graphiti 0.18.9 必须先 build_indices_and_constraints（FalkorDB 也需要）
        # 必须走 wrapper 的 background loop，否则连接绑定 stale loop
        wrapper._run_async(graphiti.build_indices_and_constraints())
        return wrapper

    return _factory
