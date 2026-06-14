"""KidsBench Harness 主控（L2）。

跑法：
    python -m harness.run_eval --questions questions/smoke.jsonl --out runs/smoke
    或直接 ./harness/run_eval.py --questions questions/smoke.jsonl

主流程（每题、每 adapter）：
1. clear(user_id)        物理清场（防幽灵记忆残留）
2. write 每个 turn       灌入历史
3. flush + consolidate   等索引就绪 + 语义固化
4. read(query)           召回 memories
5. 组 prompt → 调外层 LLM 答题
6. 双指标判分：召回率 + 答案正确性
7. 落盘 JSONL 行
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from harness.scorer import (  # noqa: E402
    JudgeResult,
    attribution_f1,
    recall_score,
    regex_judge,
)
from kidsbench.config import (  # noqa: E402
    LLMPreset,
    list_preset_names,
    load_dotenv_local,
    load_preset,
)
from kidsbench.contract import (  # noqa: E402
    AdapterError,
    MemoryAdapter,
    ReadOpts,
    Turn,
)
from kidsbench.middleware import (  # noqa: E402
    LLMClient,
    LLMResponse,
    NLIJudge,
    inject,
    judge_facts_nli,
    retry_call,
    verify_unified_injection,
)
from kidsbench.trace import (  # noqa: E402
    HttpExporter,
    JsonlExporter,
    MultiExporter,
    init_run,
    install_llm_hook,
    set_exporter,
    uninstall_llm_hook,
)
from kidsbench.trace import span as _trace_span  # noqa: E402
from kidsbench.trace import span_attr as _trace_attr  # noqa: E402
from kidsbench.trace import wrap as _trace_wrap_adapter  # noqa: E402
from kidsbench.trace.span import preview as _trace_preview  # noqa: E402

# ============= LLM 客户端（preset-based OpenAI 兼容）=============

# 兼容性别名（旧代码可能依赖）：随 --llm-preset 切换时由 main() 重赋值
GEMINI_PROXY_URL = "http://23.226.135.149:4000/v1"
GEMINI_PROXY_KEY = ""  # 启动时从 preset 注入


class ProxyLLMClient(LLMClient):
    """通用 OpenAI 兼容客户端（接受 preset）。

    Preset 决定 base_url / api_key / model / reasoning_effort 等。
    密钥从 preset.get_api_key()（env 注入，永不硬编码）。
    """

    def __init__(self, preset: LLMPreset) -> None:
        self._preset = preset
        self._model = preset.model
        self._base_url = preset.base_url
        self._api_key = preset.get_api_key()  # 抛 RuntimeError 如果未配置
        self._reasoning_effort = preset.reasoning_effort
        self._max_tokens_default = preset.max_tokens

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """通用 OpenAI 兼容 chat completion。"""
        return self._do_complete(system, user, temperature, max_tokens)

    @_trace_span("llm.answer")
    def _do_complete(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int | None,
    ) -> LLMResponse:
        import httpx

        t0 = time.perf_counter()
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens or self._max_tokens_default,
        }
        if self._reasoning_effort:
            # gemini-3.5-flash 等 thinking 模型必须显式 minimal 防 reasoning_tokens 爆 message
            body["reasoning_effort"] = self._reasoning_effort
        def _post() -> dict[str, Any]:
            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
                resp.raise_for_status()
                return resp.json()

        data = retry_call(_post, max_attempts=3, base_delay=1.0)  # 网络抖动/5xx 重试
        latency_ms = (time.perf_counter() - t0) * 1000
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"LLM 无 choices: {data}")
        msg = choices[0].get("message")
        if not msg or "content" not in msg:
            # finish_reason=length 或 reasoning 耗光时可能没 message
            fr = choices[0].get("finish_reason", "?")
            raise RuntimeError(f"LLM message 缺失（finish_reason={fr}）: {data}")
        text = msg["content"] or ""
        usage = data.get("usage", {})
        _trace_attr(
            model=self._model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=round(latency_ms, 2),
            answer_preview=_trace_preview(text, 200),
        )
        return LLMResponse(
            text=text,
            model=self._model,
            cost_token_in=usage.get("prompt_tokens", 0),
            cost_token_out=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            raw=data,
        )


# ============= Adapter 工厂 =============


def make_baseline_adapters() -> dict[str, MemoryAdapter]:
    from kidsbench.adapters import FullHistoryAdapter, NoMemoryAdapter, OracleAdapter

    adapters: dict[str, MemoryAdapter] = {
        "nomemory": NoMemoryAdapter(),
        "fullhistory": FullHistoryAdapter(),
    }
    # Oracle 需要 gold lookup，会在每题前注入
    adapters["oracle"] = OracleAdapter()
    return adapters


def make_memoryos_adapter(
    preset: LLMPreset, tmp_root: str = "/tmp/kidsbench_memoryos_eval"
) -> MemoryAdapter | None:
    """如果 .venv-memoryos 可用就返 MemoryOSAdapter，否则 None。"""
    try:
        import memoryos  # noqa: F401

        from kidsbench.adapters.memoryos_adapter import MemoryOSAdapter
    except (ImportError, ModuleNotFoundError):
        return None

    config = {
        "openai_api_key": preset.get_api_key(),
        "openai_base_url": preset.base_url,
        "data_storage_path": tmp_root,
        "llm_model": preset.model,
        "embedding_model_name": preset.embedding.model,
        "mid_term_capacity": 100,
    }
    return MemoryOSAdapter(config=config)



_EMBEDDING_SHIM_PROC = None


def _ensure_embedding_shim(port: int = 18230) -> str:
    """起本地 embedding shim（bge-small-zh-v1.5 → OpenAI /v1/embeddings）。

    为什么存在：部分被测系统（ReMe）只支持 API 形态 embedding，评测标准
    要求全员统一本地 bge-small-zh-v1.5——shim 让 API-only 系统对齐标准。
    单例子进程，healthz 就绪后返回 base_url。
    """
    global _EMBEDDING_SHIM_PROC
    import subprocess
    import urllib.request

    base = f"http://127.0.0.1:{port}"
    def _alive() -> bool:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    if _alive():
        return f"{base}/v1"
    venv_py = str(REPO_ROOT / ".venv" / "bin" / "python")
    import atexit
    _EMBEDDING_SHIM_PROC = subprocess.Popen(
        [venv_py, "-m", "kidsbench.middleware.embedding_shim", "--port", str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    atexit.register(lambda: _EMBEDDING_SHIM_PROC and _EMBEDDING_SHIM_PROC.terminate())
    # 首次加载模型可能 ~30s
    for _ in range(60):
        if _alive():
            return f"{base}/v1"
        time.sleep(1)
    raise RuntimeError("embedding shim 启动超时（60s）——检查 .venv 的 sentence-transformers")


def make_reme_adapter(preset: LLMPreset) -> MemoryAdapter | None:
    """如果 .venv-reme 可用就返 RemeAdapter（需本地 embedding shim）。"""
    try:
        import reme  # noqa: F401

        from kidsbench.adapters.reme_adapter import RemeAdapter
    except (ImportError, ModuleNotFoundError):
        return None

    shim_url = _ensure_embedding_shim()
    # reme 检索是多次 LLM 调用的 agentic 工具循环；gemini-3-flash-preview(thinking)
    # 偶返 finish=length 且无 message key（reasoning 烧光预算）→agentscope agent 崩→
    # metadata 缺 'messages'→reme 全挂。改用非 thinking 的 deepseek（满预算时也返完整
    # message 结构,不缺 key），规避 reme agentic 循环崩溃。embedding 仍走 shim。
    return RemeAdapter(config={
        "llm": {
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": os.environ.get("KIDSBENCH_DEEPSEEK_API_KEY", ""),
        },
        "embedding": {
            "model": preset.embedding.model,
            "base_url": shim_url,
            "api_key": "local-shim",
            "dimensions": preset.embedding.dim,
        },
        # working_dir 按进程唯一（codex 对抗审 #2：跨 run 残留防护第二道）
        "working_dir": f"/tmp/kidsbench_reme_eval_{os.getpid()}",
    })




def make_letta_adapter(preset: LLMPreset) -> MemoryAdapter | None:
    """如果 .venv-letta 可用 + letta server 在跑就返 LettaAdapter。

    需先跑 scripts/setup_letta_server.sh 起 server（pg0 + deepseek provider）。
    embedding 经本地 shim 对齐 bge-small-zh。
    """
    try:
        import letta_client  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        return None
    import urllib.request
    base = os.environ.get("LETTA_SERVER_URL", "http://127.0.0.1:18283")
    try:
        with urllib.request.urlopen(f"{base}/v1/health/", timeout=3) as r:
            if r.status != 200:
                return None
    except Exception:
        print("[harness] letta server 未就绪（跑 scripts/setup_letta_server.sh），跳过", flush=True)
        return None
    shim_url = _ensure_embedding_shim()
    from kidsbench.adapters.letta_adapter import LettaAdapter
    return LettaAdapter(config={
        "base_url": base,
        "model": "openai-proxy/deepseek-v4-flash",
        "embedding": {"endpoint": shim_url, "model": preset.embedding.model,
                      "dim": preset.embedding.dim},
    })


def make_graphiti_adapter(preset: LLMPreset) -> MemoryAdapter | None:
    """如果 .venv-graphiti 可用 + FalkorDB 隧道在，返回 GraphitiAdapter；否则 None。

    用法：
        ssh -f -N -L 16379:192.168.61.18:16379 mini  # 建隧道
        .venv-graphiti/bin/python -m harness.run_eval --include-graphiti
    """
    try:
        import graphiti_core  # noqa: F401

        from kidsbench.adapters.graphiti_adapter import GraphitiAdapter
        from kidsbench.middleware.graphiti_compat import (
            make_real_graphiti_client_factory,
        )
    except (ImportError, ModuleNotFoundError):
        return None

    factory = make_real_graphiti_client_factory(
        api_key=preset.get_api_key(),
        base_url=preset.base_url,
        model=preset.model,
        falkor_host="127.0.0.1",
        falkor_port=16379,
        # bge 切换后用新 database 避开旧 384d 索引污染
        falkor_database="kidsbench_bge",
        embedder_model=preset.embedding.model,
        # 跟随 preset：gemini 系需 minimal 防 thinking 耗光；deepseek 不支持
        # reasoning_effort 参数（只认 high/low/medium/max/xhigh，传 minimal 必 400），
        # preset 留空 → None → compat 层不带该参数（2026-06-12 全量首跑实锤）
        reasoning_effort=preset.reasoning_effort,
    )
    try:
        return GraphitiAdapter(
            backend="falkordb",
            uri="redis://127.0.0.1:16379",
            # skip_avx2_check: FalkorDB 远程跑在 QNAP x86（有 AVX2），
            # 本机 macOS arm64 ARM 不需检测（gemini Wave1 Graphiti.2 "AVX2 张冠李戴" finding）
            config={
                "client_factory": factory,
                "skip_avx2_check": True,
                # A 决策：传入注入的 model 供 get_injected_providers 自报
                "injected_llm_model": preset.model,
                "injected_embed_model": preset.embedding.model,
            },
        )
    except Exception as e:
        print(f"[harness] graphiti adapter 初始化失败: {e}", flush=True)
        return None


def make_mem0_adapter(preset: LLMPreset) -> MemoryAdapter | None:
    """如果 .venv-mem0 可用就返 Mem0Adapter，否则 None。

    用法：跑 harness 时必须切到 .venv-mem0 才能用 mem0。
    """
    try:
        from kidsbench.adapters.mem0_adapter import Mem0Adapter

        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    # bge 切换后用新 collection name + path 避开旧 384d 数据
                    "collection_name": "kidsbench_eval_bge",
                    "embedding_model_dims": preset.embedding.dim,
                    "path": "/tmp/kidsbench_qdrant_eval_bge",
                    "on_disk": False,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": preset.model,
                    "api_key": preset.get_api_key(),
                    "openai_base_url": preset.base_url,
                    "temperature": 0.0,
                },
            },
            "embedder": {
                "provider": preset.embedding.provider,
                "config": {
                    "model": preset.embedding.model,
                    "embedding_dims": preset.embedding.dim,
                },
            },
        }
        return Mem0Adapter(config=config, disable_telemetry=True)
    except (ImportError, ModuleNotFoundError):
        return None


# Hindsight embedded server 单例（起一次 100s+，全 run 复用；进程退出自动停）
_HINDSIGHT_SERVER = None


def _ensure_hindsight_server(preset: LLMPreset):
    """起 embedded HindsightServer（标准评测 env 五件套，见 HINDSIGHT_VERIFIED_FACTS.md）。"""
    global _HINDSIGHT_SERVER
    if _HINDSIGHT_SERVER is not None:
        return _HINDSIGHT_SERVER
    # env 必须在 server 创建前设置（config 启动时读取）
    os.environ.setdefault("HINDSIGHT_API_EMBEDDINGS_PROVIDER", "local")
    os.environ.setdefault("HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL", preset.embedding.model)
    os.environ.setdefault("HINDSIGHT_API_RERANKER_PROVIDER", "local")
    os.environ.setdefault("HINDSIGHT_API_RERANKER_LOCAL_MODEL", "BAAI/bge-reranker-v2-m3")
    # 关自动 consolidation：评测期间后台写入不受控会破坏可比性 + 产英文 observation（Phase 1.5 实测）
    os.environ.setdefault("HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION", "false")
    # reflect stage 历史注记：gemini-3 经 proxy 多轮 tool call 丢 thought_signature
    # 曾需族内降级 gemini-2.5-flash。2026-06-12 切换国产 deepseek 后该问题不存在
    # （thought_signature 是 gemini-3+Vertex 特有），reflect 跟随统一注入 preset。
    os.environ.setdefault("HINDSIGHT_API_REFLECT_LLM_PROVIDER", "openai")
    os.environ.setdefault("HINDSIGHT_API_REFLECT_LLM_MODEL", preset.model)
    os.environ.setdefault("HINDSIGHT_API_REFLECT_LLM_BASE_URL", preset.base_url)
    os.environ.setdefault("HINDSIGHT_API_REFLECT_LLM_API_KEY", preset.get_api_key())

    from hindsight import HindsightServer

    server = HindsightServer(
        db_url="pg0",
        llm_provider="openai",
        llm_base_url=preset.base_url,
        llm_api_key=preset.get_api_key(),
        llm_model=preset.model,
    )
    server.start(timeout=900)
    _HINDSIGHT_SERVER = server
    print(f"[harness] hindsight embedded server up: {server.url}", flush=True)
    return server


def make_hindsight_adapters(preset: LLMPreset) -> dict[str, MemoryAdapter] | None:
    """recall/reflect 双模式（范式旋钮）。需 .venv-hindsight；返回 None 表示不可用。"""
    try:
        import hindsight_client  # noqa: F401

        from kidsbench.adapters.hindsight_adapter import HindsightAdapter
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        server = _ensure_hindsight_server(preset)
    except Exception as e:
        print(f"[harness] hindsight server 启动失败: {e}", file=sys.stderr)
        return None
    config = {
        "base_url": server.url,
        "injected_llm_model": preset.model,
        "injected_embed_model": preset.embedding.model,
    }
    return {
        "hindsight-recall": HindsightAdapter(mode="recall", config=config),
        "hindsight-reflect": HindsightAdapter(mode="reflect", config=config),
    }


def make_memobase_adapter(preset: LLMPreset) -> MemoryAdapter | None:
    """如果 memobase client 可用 + server 在跑就返 MemobaseAdapter（画像中心）。

    需先跑 scripts/setup_memobase_server.sh（pg0+redis+源码 uvicorn，port 8019）。
    注意：memobase 内部 LLM（画像抽取）由 server config.yaml 配置（deepseek），
    非 preset 注入——能力矩阵/榜单已注明（同 graphiti 内部 LLM 处理方式）。"""
    try:
        from memobase import MemoBaseClient  # noqa: F401

        from kidsbench.adapters.memobase_adapter import MemobaseAdapter
    except (ImportError, ModuleNotFoundError):
        return None
    import urllib.request
    base = os.environ.get("MEMOBASE_SERVER_URL", "http://127.0.0.1:8019")
    token = os.environ.get("MEMOBASE_ACCESS_TOKEN", "kb-phase0-secret")
    try:
        req = urllib.request.Request(
            f"{base}/api/v1/healthcheck", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=3) as r:
            if r.status != 200:
                return None
    except Exception:
        print("[harness] memobase server 未就绪（跑 scripts/setup_memobase_server.sh），跳过", flush=True)
        return None
    _ensure_embedding_shim()  # event 检索依赖 shim（server 侧调用）
    return MemobaseAdapter(config={"base_url": base, "api_key": token})


def make_memmachine_adapter(preset: LLMPreset) -> MemoryAdapter | None:
    """如果 memmachine server 在跑就返 MemMachineAdapter（真值保存）。

    需先跑 scripts/setup_memmachine_server.sh（全 SQLite，port 8021）。
    adapter 纯 HTTP（requests），无 SDK 依赖——主 .venv 直接可跑。"""
    import urllib.request
    base = os.environ.get("MEMMACHINE_SERVER_URL", "http://127.0.0.1:8021/api/v2")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3) as r:
            if r.status != 200:
                return None
    except Exception:
        print("[harness] memmachine server 未就绪（跑 scripts/setup_memmachine_server.sh），跳过", flush=True)
        return None
    _ensure_embedding_shim()  # 写入 embedding ingest 依赖 shim（server 侧调用）
    from kidsbench.adapters.memmachine_adapter import MemMachineAdapter
    return MemMachineAdapter(config={"base_url": base})


def make_cognee_adapter(preset: LLMPreset) -> MemoryAdapter | None:
    """如果 cognee 可用就返 CogneeAdapter（多跳邻域投影）。需 .venv-cognee。

    cognee 进程内嵌入式（kuzu+LanceDB），无独立 server；LLM 走 gemini proxy
    （deepseek thinking 拒 instructor tool_choice，见 COGNEE_VERIFIED_FACTS 工程事实 #2/#3）。"""
    # ⚠️ 不能在这里 import cognee 做可用性检查：cognee import 时就初始化配置
    # （日志/LLM config 冻结），此时 LLM_API_KEY 还没被 adapter 注入 →
    # 真跑时 LLMAPIKeyNotSetError（w3 smoke 实战）。用 find_spec 探测不触发 import。
    import importlib.util

    if importlib.util.find_spec("cognee") is None:
        return None
    from kidsbench.adapters.cognee_adapter import CogneeAdapter
    shim_url = _ensure_embedding_shim()
    gemini_key = os.environ.get("KIDSBENCH_GEMINI_API_KEY", "")
    if not gemini_key:
        print("[harness] cognee 需要 KIDSBENCH_GEMINI_API_KEY（gemini proxy），跳过", flush=True)
        return None
    # KIDSBENCH_COGNEE_NO_PRUNE=1 → clear 不每题 prune（规避死锁，靠 dataset 隔离）。
    # 默认不设=保持每题 prune（与已跑全量一致），待 smoke A/B 验证无污染后再切默认。
    prune_per_clear = os.environ.get("KIDSBENCH_COGNEE_NO_PRUNE", "") != "1"
    return CogneeAdapter(config={
        "llm_model": "openai/gemini-2.5-flash",
        "llm_endpoint": "http://23.226.135.149:4000/v1",
        "llm_api_key": gemini_key,
        "embedding_endpoint": shim_url,
        "embedding_dim": preset.embedding.dim,
        "prune_per_clear": prune_per_clear,
    })


def load_questions(path: Path) -> list[dict]:
    questions = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            questions.append(json.loads(line))
    return questions


def _build_turn(t: dict) -> Turn:
    """构造 Turn。T7 题 turn 用 clean_text + noise_params（缺口1）→ 注入脏文本后灌入。"""
    if "noise_params" in t:
        np = t["noise_params"]
        text = inject(
            t["clean_text"],
            noise_type=np["type"],
            intensity=float(np["intensity"]),
            seed=int(np["seed"]),
        )
    else:
        text = t["text"]
    return Turn(
        turn_id=t["turn_id"],
        session_id=t.get("session_id", "s1"),
        role=t["role"],
        text=text,
        timestamp=float(t.get("timestamp", time.time())),
        metadata=t.get("metadata", {}),
    )


def turns_from_question(q: dict) -> list[Turn]:
    return [_build_turn(t) for t in q["turns"]]


# ============= 主流程 =============


@dataclass
class TurnLog:
    qid: str
    adapter: str
    user_id: str
    success: bool
    error: str | None
    # 召回
    recalled_count: int
    recalled_turn_ids: list[str]
    recalled_texts: list[str]
    recall_metric: dict
    # LLM
    answer: str
    llm_latency_ms: float
    llm_tokens_in: int
    llm_tokens_out: int
    # 判分
    judge_score: float
    judge_verdict: str
    judge_positive_hits: list[str]
    judge_negative_hits: list[str]
    # 元数据
    timestamp: float
    write_latency_ms: float
    read_latency_ms: float
    # Tier1 新增（默认值向后兼容）
    attribution: dict | None = None
    t6_state: str | None = None
    nli_need_human: bool = False
    # 范式成本计量（早/晚绑定 Pareto 对照；adapter 自报 usage，默认 0 向后兼容）
    adapter_write_tokens: int = 0
    adapter_read_tokens: int = 0


def setup_oracle_for_question(adapter: MemoryAdapter, q: dict) -> None:
    """Oracle 需要按当前题的 gold 注入 lookup。"""
    from kidsbench.adapters import OracleAdapter

    if not isinstance(adapter, OracleAdapter):
        return
    gold = list(q.get("gold_memory_ids", []))

    def lookup(user_id: str, query: str) -> list[str]:
        return list(gold)

    adapter.set_gold_lookup(lookup)


def _load_resume_state(out_file: Path) -> tuple[set, dict]:
    """断点续跑：从已有 results.jsonl 重建 done 集合 + summary 计数。"""
    done: set[tuple[str, str]] = set()
    summary: dict[str, dict] = {}
    for line in out_file.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        done.add((r.get("adapter", ""), r.get("qid", "")))
        s = summary.setdefault(
            r.get("adapter", ""),
            {"correct": 0, "wrong": 0, "evasive": 0, "error": 0, "total": 0},
        )
        s["total"] += 1
        if not r.get("success"):
            s["error"] += 1
        elif r.get("judge_verdict") == "correct":
            s["correct"] += 1
        elif r.get("judge_verdict") == "wrong":
            s["wrong"] += 1
        else:
            s["evasive"] += 1
    return done, summary


def render_scene_context(scene_context: dict | None) -> str:
    """把 scene_context（W3 模块A 当下感知快照）渲染成自然语言段。空则返回空串。

    scene_context 只进 prompt（让 AI 在真实场景回应），不进 turns/write、不进判分。
    """
    if not scene_context:
        return ""
    parts = [f"{k}={v}" for k, v in scene_context.items() if v]
    return f"当前场景：{' ｜ '.join(parts)}\n\n" if parts else ""


def render_current_session(current_session: list[dict] | None) -> str:
    """渲染「当前对话」段（协议 v1.1：T+0 当场对话在 LLM context 里）。

    这段模拟产品 runtime 的会话上下文——孩子刚说过的话被测系统必须看得见，
    否则回应脱节、判分失真（扣的是评测环境的分，不是记忆能力的分）。
    它只进 prompt，不调 adapter.write、不进判分（与 scene_context 同三边界）。
    空/缺省 → 返回空串，旧 124 题（无 current_session 字段）行为不变。
    """
    if not current_session:
        return ""
    role_names = {"assistant": "小可", "user": "孩子", "system": "系统"}
    lines = [
        f"{role_names.get(t.get('role', 'user'), t.get('speaker', '孩子'))}: {t['text']}"
        for t in current_session
    ]
    return "当前对话（本次会话刚刚发生）：\n" + "\n".join(lines) + "\n\n"


def _is_event_trigger(query: str) -> bool:
    """新题库的触发形态：[系统事件] 传感器事件 / [孩子] 主动发话。

    旧 124 题的 query 是自然问句（「我最爱的恐龙是哪个来着？」），
    新题库 v0.1 的触发是事件描述——两种形态需要不同的回应模式。
    """
    return query.lstrip().startswith(("[系统事件]", "[孩子]", "[家长消息]"))


def build_prompt(
    query: str, memories: list[dict], scene_context: dict | None = None,
    current_session: list[dict] | None = None,
) -> tuple[str, str]:
    """组 prompt。scene_context / current_session 默认 None → 完全向后兼容。

    段落顺序（协议 v1.1）：当前场景 → 相关记忆 → 当前对话 → 触发输入。

    双模式 system（2026-06-11 smoke 实测教训）：
    - 问答式（旧 124 题）：用户主动提问 → 「结合记忆回答问题」，原 prompt 逐字不动
    - 事件触发式（新题库）：系统事件/孩子发话 → 「主动关怀 + 自然融入记忆」。
      首跑 smoke 实测：问答式 prompt 下连 Oracle（直接喂正确记忆）都 11/12
      不使用记忆（只回应眼前事件，危机记忆也被无视）——回应模式错配是
      评测环境问题，对所有被测系统一致修正，不影响横评公平性。
    """
    scene_block = render_scene_context(scene_context)
    current_block = render_current_session(current_session)
    if _is_event_trigger(query):
        system = (
            "你是「小可」，孩子的 K12 学习陪伴伙伴（不是冷冰冰的助手）。"
            "现在发生了一个事件（见「当前事件」），请你像了解这个孩子的老朋友一样回应。"
            "要求："
            "1. 仔细看「相关记忆」——如果其中有与当前情境相关的内容（孩子的名字、"
            "兴趣、约定、近况、情绪），必须自然地融入回应，体现你记得他/她；"
            "2. 记忆里没有的事绝不编造；记忆与当前情境无关时不强行提及；"
            "3. 如果记忆中有孩子的安全或情绪危机信号，必须保持警觉并体现关怀；"
            "4. 用温暖的伙伴口吻，60 字以内。"
        )
        query_label = "当前事件"
    else:
        # 旧 124 题问答式：与冻结版逐字相同（保回归）
        scene_hint = "「当前场景」和" if scene_block else ""
        current_hint = "「当前对话」和" if current_block else ""
        system = (
            f"你是 K12 儿童 AI 陪伴助手。请结合{scene_hint}{current_hint}"
            "「相关记忆」简短回答用户的问题。"
            "如果记忆里没有相关信息，请直接说不知道，不要编造。"
            "回答控制在 30 字以内。"
        )
        query_label = "用户问题"
    if memories:
        context = "\n".join(f"- {m['text']}" for m in memories)
        memory_block = f"相关记忆：\n{context}"
    else:
        memory_block = "相关记忆：（无）"
    user = (f"{scene_block}{memory_block}\n\n{current_block}"
            f"{query_label}：{query}\n\n你的回答：")
    return system, user



import re as _re

_CIRCLED_NUMS = str.maketrans("①②③④⑤⑥⑦⑧⑨⑩", "1234567890")


def _safe_user_id(adapter_name: str, qid: str) -> str:
    """user_id ASCII 安全化。

    新题库 qid 含圈数字（S14-⑤-008），直接拼进 user_id 会炸 graphiti 的
    FalkorDB RediSearch 查询语法（2026-06-12 全量首跑 149 题全 clear_failed）。
    圈数字转普通数字 + 其余非词字符转下划线。只影响存储隔离标识，
    resume 按 (adapter,qid) 记账不受影响。
    """
    safe = _re.sub(r"[^\w\-]", "_", qid.translate(_CIRCLED_NUMS))
    return f"eval_{adapter_name}_{safe}"


def evaluate_one(
    adapter: MemoryAdapter,
    q: dict,
    llm_client: LLMClient,
    nli: NLIJudge | None = None,
) -> TurnLog:
    # T5/T6 双阶段题走 phases 编排（缺口2）
    if "phases" in q:
        return evaluate_phased(adapter, q, llm_client, nli)

    qid = q["qid"]
    user_id = _safe_user_id(adapter.name, qid)
    ts = time.time()

    # 1. 每题前注入 Oracle gold（如果是 Oracle）
    setup_oracle_for_question(adapter, q)

    # 2. 物理清场（防上一题幽灵记忆）
    try:
        adapter.clear(user_id)
    except (AdapterError, Exception) as e:
        return TurnLog(
            qid=qid, adapter=adapter.name, user_id=user_id,
            success=False, error=f"clear_failed: {type(e).__name__}: {e}",
            recalled_count=0, recalled_turn_ids=[], recalled_texts=[],
            recall_metric={}, answer="", llm_latency_ms=0.0,
            llm_tokens_in=0, llm_tokens_out=0,
            judge_score=0.0, judge_verdict="error",
            judge_positive_hits=[], judge_negative_hits=[],
            timestamp=ts, write_latency_ms=0.0, read_latency_ms=0.0,
        )

    # 3. 灌历史
    turns = turns_from_question(q)
    write_latency = 0.0
    write_tokens = 0
    try:
        for turn in turns:
            ws = adapter.write(user_id, turn)
            write_latency += ws.latency_ms
            write_tokens += ws.cost_token
    except (AdapterError, Exception) as e:
        return TurnLog(
            qid=qid, adapter=adapter.name, user_id=user_id,
            success=False, error=f"write_failed: {type(e).__name__}: {e}",
            recalled_count=0, recalled_turn_ids=[], recalled_texts=[],
            recall_metric={}, answer="", llm_latency_ms=0.0,
            llm_tokens_in=0, llm_tokens_out=0,
            judge_score=0.0, judge_verdict="error",
            judge_positive_hits=[], judge_negative_hits=[],
            timestamp=ts, write_latency_ms=write_latency, read_latency_ms=0.0,
        )

    # 4. flush + consolidate
    try:
        adapter.flush(user_id)
        adapter.consolidate(user_id)
    except (AdapterError, Exception) as e:
        # 不致命，继续
        print(f"  [warn] {adapter.name}/{qid} flush/consolidate err: {e}", file=sys.stderr)

    # 5. 召回
    try:
        rr = adapter.read(
            user_id, q["query"], ReadOpts(top_k=5, current_timestamp=q.get("current_timestamp"))
        )
        recalled_turn_ids = sorted({
            tid for m in rr.memories for tid in (m.source_turn_ids or [])
        })
        recalled_texts = [m.text for m in rr.memories]
        read_latency = rr.latency_ms
        read_tokens = rr.cost_token
    except (AdapterError, Exception) as e:
        return TurnLog(
            qid=qid, adapter=adapter.name, user_id=user_id,
            success=False, error=f"read_failed: {type(e).__name__}: {e}",
            recalled_count=0, recalled_turn_ids=[], recalled_texts=[],
            recall_metric={}, answer="", llm_latency_ms=0.0,
            llm_tokens_in=0, llm_tokens_out=0,
            judge_score=0.0, judge_verdict="error",
            judge_positive_hits=[], judge_negative_hits=[],
            timestamp=ts, write_latency_ms=write_latency, read_latency_ms=0.0,
        )

    rmetric = recall_score(recalled_turn_ids, q.get("gold_memory_ids", []))
    attribution = attribution_f1(
        recalled_turn_ids, q.get("gold_memory_ids", []), q.get("fact_distribution", "single")
    )

    # 6. 组 prompt + 调 LLM
    memory_dicts = [{"text": t} for t in recalled_texts]
    system_p, user_p = build_prompt(q["query"], memory_dicts, q.get("scene_context"),
                                    q.get("current_session"))
    try:
        llm_resp = llm_client.complete(system=system_p, user=user_p, temperature=0.0, max_tokens=4096)
        answer = llm_resp.text.strip()
    except Exception as e:
        return TurnLog(
            qid=qid, adapter=adapter.name, user_id=user_id,
            success=False, error=f"llm_failed: {type(e).__name__}: {e}",
            recalled_count=len(rr.memories), recalled_turn_ids=recalled_turn_ids,
            recalled_texts=recalled_texts, recall_metric=rmetric,
            answer="", llm_latency_ms=0.0, llm_tokens_in=0, llm_tokens_out=0,
            judge_score=0.0, judge_verdict="error",
            judge_positive_hits=[], judge_negative_hits=[],
            timestamp=ts, write_latency_ms=write_latency, read_latency_ms=read_latency,
        )

    # 7. 判分（新题型 expected_facts 走 NLI，旧题 expected_answer_points 走 regex）
    jd = _judge_answer(answer, q, nli)

    # 8. 清场（评测后清干净）
    try:
        adapter.clear(user_id)
    except Exception:
        pass

    return TurnLog(
        qid=qid, adapter=adapter.name, user_id=user_id,
        success=True, error=None,
        recalled_count=len(rr.memories),
        recalled_turn_ids=recalled_turn_ids,
        recalled_texts=recalled_texts,
        recall_metric=rmetric,
        answer=answer,
        llm_latency_ms=llm_resp.latency_ms,
        llm_tokens_in=llm_resp.cost_token_in,
        llm_tokens_out=llm_resp.cost_token_out,
        judge_score=jd["score"],
        judge_verdict=jd["verdict"],
        judge_positive_hits=jd["positive_hits"],
        judge_negative_hits=jd["negative_hits"],
        timestamp=ts,
        write_latency_ms=write_latency,
        read_latency_ms=read_latency,
        attribution=attribution,
        nli_need_human=jd["need_human"],
        adapter_write_tokens=write_tokens,
        adapter_read_tokens=read_tokens,
    )


def _judge_answer(answer: str, q: dict, nli: NLIJudge | None) -> dict:
    """统一判分：有 nli + expected_facts → NLI 蕴含；否则 regex（旧 smoke 兼容）。

    negative_only 题（该遗忘型，如 S08 周报「不该翻负面旧事」）：
    expected_facts 为空是设计意图，判分只查雷区——答案蕴含任一 negative
    即 wrong，否则 correct。注意此类题差异主要在生成端选择性表达，
    对记忆后端归因弱（报告侧标注）。
    """
    if nli is not None and q.get("judgment_mode") == "negative_only":
        r = judge_facts_nli(answer, [], q.get("negative_facts", []), nli)
        hit_negative = bool(r["negative_hits"])
        return {
            "score": 0.0 if hit_negative else 1.0,
            "verdict": "wrong" if hit_negative else "correct",
            "positive_hits": [], "negative_hits": r["negative_hits"],
            "need_human": r["need_human"],
        }
    if nli is not None and q.get("expected_facts"):
        r = judge_facts_nli(answer, q.get("expected_facts", []), q.get("negative_facts", []), nli)
        return {
            "score": r["score"], "verdict": r["verdict"],
            "positive_hits": r["positive_hits"], "negative_hits": r["negative_hits"],
            "need_human": r["need_human"],
        }
    jr: JudgeResult = regex_judge(answer, q)
    return {
        "score": jr.score, "verdict": jr.verdict,
        "positive_hits": jr.positive_hits, "negative_hits": jr.negative_hits, "need_human": False,
    }


def _read_and_answer(
    adapter: MemoryAdapter, query: str, llm_client: LLMClient, user_id: str,
    current_timestamp: float | None, scene_context: dict | None = None,
) -> dict:
    """read → 组 prompt → 调 LLM。返回召回 + 答案。"""
    rr = adapter.read(user_id, query, ReadOpts(top_k=5, current_timestamp=current_timestamp))
    recalled_turn_ids = sorted({tid for m in rr.memories for tid in (m.source_turn_ids or [])})
    texts = [m.text for m in rr.memories]
    system_p, user_p = build_prompt(query, [{"text": t} for t in texts], scene_context)
    resp = llm_client.complete(system=system_p, user=user_p, temperature=0.0, max_tokens=4096)
    return {
        "answer": resp.text.strip(), "recalled_turn_ids": recalled_turn_ids,
        "recalled_texts": texts, "n_mem": len(rr.memories), "resp": resp,
        "read_latency": rr.latency_ms,
    }


def _phased_error_log(qid, adapter_name, user_id, error, ts, write_latency=0.0) -> TurnLog:
    return TurnLog(
        qid=qid, adapter=adapter_name, user_id=user_id, success=False, error=error,
        recalled_count=0, recalled_turn_ids=[], recalled_texts=[], recall_metric={},
        answer="", llm_latency_ms=0.0, llm_tokens_in=0, llm_tokens_out=0,
        judge_score=0.0, judge_verdict="error", judge_positive_hits=[], judge_negative_hits=[],
        timestamp=ts, write_latency_ms=write_latency, read_latency_ms=0.0,
    )


def score_t6(control_answer, control_expected, scenario_answer, expected_facts, nli) -> dict:
    """T6 三态。一票否决(FAIL)只由 control 失败触发（硬召回判定）；
    scenario 软判定只决定 pass↔WARN，永不产生 FAIL（缺口4，软判定隔离）。
    """
    if nli is None:
        return {"t6_state": "SKIP_no_nli", "control_success": None,
                "scenario_alert": None, "need_human": True}
    ctrl_hyp = control_expected.get("hypothesis", "") if control_expected else ""
    ctrl_res = nli.entail(control_answer, ctrl_hyp) if ctrl_hyp else None
    control_success = bool(ctrl_res and ctrl_res.is_entailment)
    scen_res = nli.entail(scenario_answer, expected_facts[0]["hypothesis"]) if expected_facts else None
    scenario_alert = bool(scen_res and scen_res.is_entailment)
    if not control_success:
        state = "FAIL_swallowed"
    elif scenario_alert:
        state = "pass"
    else:
        state = "WARN_no_alert"
    need_human = any(r is not None and r.low_confidence for r in (ctrl_res, scen_res))
    return {"t6_state": state, "control_success": control_success,
            "scenario_alert": scenario_alert, "need_human": need_human}


def evaluate_phased(
    adapter: MemoryAdapter, q: dict, llm_client: LLMClient, nli: NLIJudge | None = None,
) -> TurnLog:
    """T5/T6 双阶段编排：ingest → consolidate(trigger) → probe（缺口2/3）。"""
    qid = q["qid"]
    user_id = _safe_user_id(adapter.name, qid)
    ts = time.time()
    setup_oracle_for_question(adapter, q)
    try:
        adapter.clear(user_id)
    except Exception as e:
        return _phased_error_log(qid, adapter.name, user_id, f"clear_failed: {e}", ts)

    write_latency = 0.0
    probe_queries: dict | None = None
    try:
        for ph in q["phases"]:
            phase = ph.get("phase")
            if phase == "ingest":
                for t in ph.get("turns", []):
                    ws = adapter.write(user_id, _build_turn(t))
                    write_latency += ws.latency_ms
            elif phase == "consolidate" and ph.get("trigger_consolidate"):
                adapter.flush(user_id)        # flush gate
                adapter.consolidate(user_id)
            elif phase == "probe":
                probe_queries = ph.get("queries", {})
    except Exception as e:
        return _phased_error_log(qid, adapter.name, user_id, f"phase_failed: {e}", ts, write_latency)

    if not probe_queries:
        return _phased_error_log(qid, adapter.name, user_id, "no_probe_phase", ts, write_latency)

    ct = q.get("current_timestamp")
    try:
        if "control_query" in probe_queries:
            return _run_t6(adapter, q, probe_queries, llm_client, nli, user_id, ts, write_latency, ct)
        return _run_t5(adapter, q, probe_queries, llm_client, nli, user_id, ts, write_latency, ct)
    except Exception as e:
        return _phased_error_log(qid, adapter.name, user_id, f"probe_failed: {e}", ts, write_latency)
    finally:
        try:
            adapter.clear(user_id)
        except Exception:
            pass


def _run_t5(adapter, q, queries, llm_client, nli, user_id, ts, write_latency, ct) -> TurnLog:
    """T5 单 query：read+answer → NLI 判内容未失真 + Attribution。"""
    res = _read_and_answer(
        adapter, queries.get("query", ""), llm_client, user_id, ct, q.get("scene_context")
    )
    jd = _judge_answer(res["answer"], q, nli)
    attribution = attribution_f1(
        res["recalled_turn_ids"], q.get("gold_memory_ids", []), q.get("fact_distribution", "single")
    )
    rmetric = recall_score(res["recalled_turn_ids"], q.get("gold_memory_ids", []))
    return TurnLog(
        qid=q["qid"], adapter=adapter.name, user_id=user_id, success=True, error=None,
        recalled_count=res["n_mem"], recalled_turn_ids=res["recalled_turn_ids"],
        recalled_texts=res["recalled_texts"], recall_metric=rmetric, answer=res["answer"],
        llm_latency_ms=res["resp"].latency_ms, llm_tokens_in=res["resp"].cost_token_in,
        llm_tokens_out=res["resp"].cost_token_out, judge_score=jd["score"],
        judge_verdict=jd["verdict"], judge_positive_hits=jd["positive_hits"],
        judge_negative_hits=jd["negative_hits"], timestamp=ts, write_latency_ms=write_latency,
        read_latency_ms=res["read_latency"], attribution=attribution, nli_need_human=jd["need_human"],
    )


def _run_t6(adapter, q, queries, llm_client, nli, user_id, ts, write_latency, ct) -> TurnLog:
    """T6 双 query 三态：control + scenario → pass/WARN/FAIL。"""
    sc = q.get("scene_context")
    ctrl = _read_and_answer(adapter, queries["control_query"], llm_client, user_id, ct, sc)
    scen = _read_and_answer(adapter, queries["scenario_query"], llm_client, user_id, ct, sc)
    t6 = score_t6(
        ctrl["answer"], queries.get("control_expected", {}), scen["answer"],
        q.get("expected_facts", []), nli,
    )
    recalled = sorted(set(ctrl["recalled_turn_ids"]) | set(scen["recalled_turn_ids"]))
    attribution = attribution_f1(
        ctrl["recalled_turn_ids"], q.get("gold_memory_ids", []), q.get("fact_distribution", "single")
    )
    rmetric = recall_score(recalled, q.get("gold_memory_ids", []))
    verdict = "correct" if t6["t6_state"] == "pass" else "wrong"
    return TurnLog(
        qid=q["qid"], adapter=adapter.name, user_id=user_id, success=True, error=None,
        recalled_count=ctrl["n_mem"] + scen["n_mem"], recalled_turn_ids=recalled,
        recalled_texts=ctrl["recalled_texts"] + scen["recalled_texts"], recall_metric=rmetric,
        answer=f"[control] {ctrl['answer']}\n[scenario] {scen['answer']}",
        llm_latency_ms=ctrl["resp"].latency_ms + scen["resp"].latency_ms,
        llm_tokens_in=ctrl["resp"].cost_token_in + scen["resp"].cost_token_in,
        llm_tokens_out=ctrl["resp"].cost_token_out + scen["resp"].cost_token_out,
        judge_score=1.0 if t6["t6_state"] == "pass" else 0.0, judge_verdict=verdict,
        judge_positive_hits=[], judge_negative_hits=[], timestamp=ts, write_latency_ms=write_latency,
        read_latency_ms=ctrl["read_latency"] + scen["read_latency"],
        attribution=attribution, t6_state=t6["t6_state"], nli_need_human=t6["need_human"],
    )


def _post_trace_complete(event_tpl: str, run_id: str, headers: dict[str, str]) -> None:
    """通知 SSE backend run 结束（POST .../api/run/{run_id}/complete）。

    从 event 模板推导 complete URL（替换尾部 /event → /complete）。失败静默。
    """
    import urllib.error
    import urllib.request

    try:
        event_url = event_tpl.format(run_id=run_id)
        complete_url = event_url.rsplit("/event", 1)[0] + "/complete"
        req = urllib.request.Request(
            complete_url,
            data=b"{}",
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0):
            pass
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, Exception):
        pass  # complete 通知失败不阻断评测


def main() -> int:
    parser = argparse.ArgumentParser(description="KidsBench Harness L2 主控")
    parser.add_argument("--questions", type=Path, default=Path("questions/smoke.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("--include-mem0", action="store_true", help="是否跑 Mem0Adapter（需 .venv-mem0）")
    parser.add_argument("--include-memoryos", action="store_true", help="是否跑 MemoryOSAdapter（需 .venv-memoryos）")
    parser.add_argument("--include-graphiti", action="store_true", help="是否跑 GraphitiAdapter（需 .venv-graphiti + 隧道 16379→QNAP）")
    parser.add_argument("--include-hindsight", action="store_true", help="是否跑 Hindsight recall/reflect 双模式（需 .venv-hindsight，embedded pg0）")
    parser.add_argument("--run-id", type=str, default=f"run_{int(time.time())}")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="启用 B1 trace：每 (adapter, qid) 生成 pipeline.jsonl + 可选 HTTP POST 实时推送",
    )
    parser.add_argument(
        "--trace-http",
        type=str,
        default="",
        help="trace HTTP exporter endpoint 模板（含 {run_id} 占位），默认仅本地 jsonl",
    )
    parser.add_argument(
        "--trace-http-auth",
        type=str,
        default="",
        help="公网 SSE backend 的 Basic Auth，格式 user:pass（经 nginx 时需要）",
    )
    parser.add_argument(
        "--llm-preset",
        type=str,
        default="gemini-3-flash",
        help="LLM preset 名（configs/llm_presets/<name>.toml）。默认 gemini-3.5-flash",
    )
    parser.add_argument(
        "--list-llm-presets",
        action="store_true",
        help="列出所有可用 preset 后退出",
    )
    parser.add_argument(
        "--judge-preset",
        type=str,
        default=None,
        help="NLI judge preset（如 qwen-judge）。给了则新题型 expected_facts 走 NLI 蕴含判分；"
        "不给则走 regex（旧 smoke 题）。judge 必须独立于被测 LLM。",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：读已有 results.jsonl，跳过已完成的 (adapter,qid)，追加写（防中断从头跑）。",
    )
    parser.add_argument(
        "--include-letta",
        action="store_true",
        help="是否跑 LettaAdapter（需 .venv-letta + letta server 在跑，"
        "先 bash scripts/setup_letta_server.sh）",
    )
    parser.add_argument(
        "--include-reme",
        action="store_true",
        help="是否跑 RemeAdapter（需 .venv-reme：pip install reme-ai agentscope==1.0.20；"
        "embedding 经本地 shim 对齐 bge-small-zh）",
    )
    parser.add_argument(
        "--include-memobase",
        action="store_true",
        help="是否跑 MemobaseAdapter（画像中心；需 server：scripts/setup_memobase_server.sh，"
        "client：pip install memobase 进当前 venv）",
    )
    parser.add_argument(
        "--include-memmachine",
        action="store_true",
        help="是否跑 MemMachineAdapter（真值保存；需 server：scripts/setup_memmachine_server.sh，"
        "adapter 纯 HTTP 无 SDK 依赖）",
    )
    parser.add_argument(
        "--include-cognee",
        action="store_true",
        help="是否跑 CogneeAdapter（多跳；需 .venv-cognee：pip install cognee 'mistralai>=1.5,<2'，"
        "进程内嵌入式无独立 server）",
    )
    parser.add_argument(
        "--skip-baselines",
        action="store_true",
        help="跳过 nomemory/fullhistory/oracle 基线（多 venv 分批跑时基线只需跑一次，"
        "避免重复烧 API）。",
    )
    args = parser.parse_args()

    # 加载 .env.local（含 KIDSBENCH_*_API_KEY 等 secret，已 chmod 600 + .gitignored）
    load_dotenv_local()

    if args.list_llm_presets:
        print("可用 LLM presets:")
        for name in list_preset_names():
            try:
                p = load_preset(name)
                marker = "✓" if p.is_configured() else "✗（KEY 未配置）"
                print(f"  {marker}  {name}  →  {p.display_name}  [{p.api_key_env}]")
            except Exception as e:
                print(f"  ⚠  {name}  →  parse error: {e}")
        return 0

    try:
        preset = load_preset(args.llm_preset)
    except ValueError as e:
        print(f"[harness] {e}", file=sys.stderr)
        return 2
    print(f"[harness] LLM preset: {preset.display_name} ({preset.api_key_env})", flush=True)

    # trace HTTP 推送的 auth header（公网经 nginx Basic Auth 时需要）
    _trace_http_headers: dict[str, str] = {}
    if args.trace_http_auth:
        import base64

        token = base64.b64encode(args.trace_http_auth.encode()).decode()
        _trace_http_headers["Authorization"] = f"Basic {token}"

    # 给 mem0/memoryos/graphiti factory 用
    global GEMINI_PROXY_URL, GEMINI_PROXY_KEY
    GEMINI_PROXY_URL = preset.base_url
    try:
        GEMINI_PROXY_KEY = preset.get_api_key()
    except RuntimeError as e:
        print(f"[harness] {e}", file=sys.stderr)
        return 2

    questions = load_questions(args.questions)
    print(f"[harness] loaded {len(questions)} questions from {args.questions}", flush=True)

    adapters = {} if args.skip_baselines else make_baseline_adapters()
    if args.skip_baselines:
        print("[harness] --skip-baselines：跳过 nomemory/fullhistory/oracle", flush=True)
    if args.include_mem0:
        mem0 = make_mem0_adapter(preset)
        if mem0 is None:
            print("[harness] mem0 不可用（未装 mem0ai 或 sentence-transformers），跳过", flush=True)
        else:
            adapters["mem0"] = mem0
    if args.include_memoryos:
        memoryos = make_memoryos_adapter(preset, f"/tmp/kidsbench_memoryos_{args.run_id}")
        if memoryos is None:
            print("[harness] memoryos 不可用（未装 memoryos package），跳过", flush=True)
        else:
            adapters["memoryos"] = memoryos
    if args.include_letta:
        letta_a = make_letta_adapter(preset)
        if letta_a is None:
            print("[harness] letta 不可用（无 server/未装），跳过", flush=True)
        else:
            adapters["letta"] = letta_a
    if args.include_reme:
        reme_a = make_reme_adapter(preset)
        if reme_a is None:
            print("[harness] reme 不可用（未装 reme-ai），跳过", flush=True)
        else:
            adapters["reme"] = reme_a
    if args.include_graphiti:
        graphiti = make_graphiti_adapter(preset)
        if graphiti is None:
            print("[harness] graphiti 不可用（未装 graphiti-core 或隧道未起），跳过", flush=True)
        else:
            adapters["graphiti"] = graphiti
    if args.include_hindsight:
        hindsight_pair = make_hindsight_adapters(preset)
        if hindsight_pair is None:
            print("[harness] hindsight 不可用（未装 hindsight-client 或 server 起不来），跳过", flush=True)
        else:
            adapters.update(hindsight_pair)
    if args.include_memobase:
        memobase_a = make_memobase_adapter(preset)
        if memobase_a is None:
            print("[harness] memobase 不可用（无 server/未装 client），跳过", flush=True)
        else:
            adapters["memobase"] = memobase_a
    if args.include_memmachine:
        memmachine_a = make_memmachine_adapter(preset)
        if memmachine_a is None:
            print("[harness] memmachine 不可用（server 未就绪），跳过", flush=True)
        else:
            adapters["memmachine"] = memmachine_a
    if args.include_cognee:
        cognee_a = make_cognee_adapter(preset)
        if cognee_a is None:
            print("[harness] cognee 不可用（未装 cognee 或缺 gemini key），跳过", flush=True)
        else:
            adapters["cognee"] = cognee_a

    print(f"[harness] adapters: {list(adapters.keys())}", flush=True)
    llm = ProxyLLMClient(preset)

    # NLI judge（B 决策）：给了 --judge-preset 才启用，独立于被测 LLM
    nli: NLIJudge | None = None
    if args.judge_preset:
        nli = NLIJudge.from_preset(args.judge_preset)
        print(f"[harness] NLI judge: {args.judge_preset}", flush=True)

    # A 决策：运行时校验三家注入的 LLM/embedding 是否统一（公平性命根）
    injection = verify_unified_injection(adapters, preset.model, preset.embedding.model)
    for x in injection:
        if x["status"] == "mismatch":
            print(
                f"[harness] ⚠️ {x['adapter']} 注入不一致："
                f"llm={x['injected_llm']} embed={x['injected_embed']} "
                f"(期望 {preset.model}/{preset.embedding.model}) → 应隔离 Model-Locked",
                flush=True,
            )
    locked = [x["adapter"] for x in injection if x["status"] == "ok"]
    if locked:
        print(f"[harness] 统一锁定校验通过: {locked} 已锁 {preset.model}", flush=True)

    run_dir = args.out / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out_file = run_dir / "results.jsonl"

    # B1.1 第二刀：trace 启用时 monkey-patch openai + sentence_transformers
    # 让 mem0/memoryos/graphiti 内部 LLM/embedding 调用自动产生 span
    if args.trace:
        install_llm_hook()

    summary: dict[str, dict] = {}
    done: set[tuple[str, str]] = set()
    file_mode = "w"
    if args.resume and out_file.exists():
        done, summary = _load_resume_state(out_file)
        file_mode = "a"
        print(f"[harness] resume: 已完成 {len(done)} 个 (adapter,qid)，续跑剩余", flush=True)

    with out_file.open(file_mode, encoding="utf-8") as fout:
        for adapter_name, adapter in adapters.items():
            print(f"\n=== [{adapter_name}] ===", flush=True)
            summary.setdefault(
                adapter_name, {"correct": 0, "wrong": 0, "evasive": 0, "error": 0, "total": 0}
            )
            # B1: trace 启用时包 adapter，方法调用自动发 span
            traced_adapter = _trace_wrap_adapter(adapter) if args.trace else adapter
            for q in questions:
                qid = q["qid"]
                if (adapter_name, qid) in done:
                    continue  # 断点续跑：已完成跳过
                print(f"  {qid} ({q.get('difficulty_class','?')}, {q.get('cognitive_type','?')})...",
                      flush=True, end=" ")
                try:
                    if args.trace:
                        # 每 (adapter, qid) 一个独立 run_id + 独立 pipeline.jsonl
                        trace_run_id = f"{adapter_name}_{qid}_{args.run_id}"
                        pipeline_path = run_dir / "pipelines" / f"{trace_run_id}.jsonl"
                        pipeline_path.parent.mkdir(parents=True, exist_ok=True)
                        exporters = [JsonlExporter(pipeline_path)]
                        if args.trace_http:
                            # 闭包捕获字面 trace_run_id（默认参数早绑定）。
                            # 不能用 get_current_run_id：HttpExporter 后台线程
                            # 不继承 contextvars，会拿到 None 静默丢弃所有 POST。
                            exporters.append(HttpExporter(
                                endpoint_tpl=args.trace_http,
                                run_id_getter=(lambda rid=trace_run_id: rid),
                                extra_headers=_trace_http_headers,
                            ))
                        set_exporter(MultiExporter(exporters))
                        with init_run(trace_run_id, qid=qid, adapter=adapter_name, run_group=args.run_id):
                            log = evaluate_one(traced_adapter, q, llm, nli)
                        set_exporter(None)
                        # 通知 SSE backend 该 run 结束（让前端实时页收 complete 帧）
                        if args.trace_http:
                            _post_trace_complete(args.trace_http, trace_run_id, _trace_http_headers)
                    else:
                        log = evaluate_one(traced_adapter, q, llm, nli)
                except Exception as e:
                    print(f"FATAL: {e}", flush=True)
                    traceback.print_exc()
                    continue
                fout.write(json.dumps(asdict(log), ensure_ascii=False) + "\n")
                fout.flush()

                summary[adapter_name]["total"] += 1
                if not log.success:
                    summary[adapter_name]["error"] += 1
                    print(f"ERROR ({log.error[:50]})", flush=True)
                elif log.judge_verdict == "correct":
                    summary[adapter_name]["correct"] += 1
                    print(f"✓ score={log.judge_score} answer={log.answer[:30]}", flush=True)
                elif log.judge_verdict == "wrong":
                    summary[adapter_name]["wrong"] += 1
                    print(f"✗ wrong (neg hit) answer={log.answer[:30]}", flush=True)
                else:
                    summary[adapter_name]["evasive"] += 1
                    print(f"~ evasive answer={log.answer[:30]}", flush=True)

    # 总结
    print("\n=== 总结表 ===", flush=True)
    print(f"{'adapter':<14}{'correct':>9}{'wrong':>8}{'evasive':>9}{'error':>8}{'acc':>7}", flush=True)
    for name, s in summary.items():
        total = max(1, s["total"])
        acc = s["correct"] / total
        print(f"{name:<14}{s['correct']:>9}{s['wrong']:>8}{s['evasive']:>9}{s['error']:>8}{acc:>7.2f}",
              flush=True)

    summary_file = run_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[harness] results → {out_file}", flush=True)
    print(f"[harness] summary → {summary_file}", flush=True)

    # B1.1 第二刀：还原 monkey-patch
    if args.trace:
        uninstall_llm_hook()

    return 0


if __name__ == "__main__":
    sys.exit(main())
