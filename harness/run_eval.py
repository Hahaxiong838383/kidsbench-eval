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
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from harness.scorer import JudgeResult, recall_score, regex_judge  # noqa: E402
from kidsbench.contract import (  # noqa: E402
    AdapterError,
    MemoryAdapter,
    ReadOpts,
    Turn,
)
from kidsbench.config import LLMPreset, list_preset_names, load_dotenv_local, load_preset  # noqa: E402
from kidsbench.middleware import LLMClient, LLMResponse  # noqa: E402
from kidsbench.trace import (  # noqa: E402
    HttpExporter,
    JsonlExporter,
    MultiExporter,
    init_run,
    install_llm_hook,
    set_exporter,
    span as _trace_span,
    span_attr as _trace_attr,
    uninstall_llm_hook,
    wrap as _trace_wrap_adapter,
)
from kidsbench.trace.span import get_current_run_id, preview as _trace_preview  # noqa: E402

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
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
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
        reasoning_effort=preset.reasoning_effort or "minimal",
    )
    try:
        return GraphitiAdapter(
            backend="falkordb",
            uri="redis://127.0.0.1:16379",
            # skip_avx2_check: FalkorDB 远程跑在 QNAP x86（有 AVX2），
            # 本机 macOS arm64 ARM 不需检测（gemini Wave1 Graphiti.2 "AVX2 张冠李戴" finding）
            config={"client_factory": factory, "skip_avx2_check": True},
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


def load_questions(path: Path) -> list[dict]:
    questions = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            questions.append(json.loads(line))
    return questions


def turns_from_question(q: dict) -> list[Turn]:
    return [
        Turn(
            turn_id=t["turn_id"],
            session_id=t.get("session_id", "s1"),
            role=t["role"],
            text=t["text"],
            timestamp=float(t.get("timestamp", time.time())),
            metadata=t.get("metadata", {}),
        )
        for t in q["turns"]
    ]


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


def setup_oracle_for_question(adapter: MemoryAdapter, q: dict) -> None:
    """Oracle 需要按当前题的 gold 注入 lookup。"""
    from kidsbench.adapters import OracleAdapter

    if not isinstance(adapter, OracleAdapter):
        return
    gold = list(q.get("gold_memory_ids", []))

    def lookup(user_id: str, query: str) -> list[str]:
        return list(gold)

    adapter.set_gold_lookup(lookup)


def build_prompt(query: str, memories: list[dict]) -> tuple[str, str]:
    """组 prompt。"""
    system = (
        "你是 K12 儿童 AI 陪伴助手。请基于「相关记忆」简短回答用户的问题。"
        "如果记忆里没有相关信息，请直接说不知道，不要编造。"
        "回答控制在 30 字以内。"
    )
    if memories:
        context = "\n".join(f"- {m['text']}" for m in memories)
        memory_block = f"相关记忆：\n{context}"
    else:
        memory_block = "相关记忆：（无）"
    user = f"{memory_block}\n\n用户问题：{query}\n\n你的回答："
    return system, user


def evaluate_one(
    adapter: MemoryAdapter,
    q: dict,
    llm_client: LLMClient,
) -> TurnLog:
    qid = q["qid"]
    user_id = f"eval_{adapter.name}_{qid}"
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
    try:
        for turn in turns:
            ws = adapter.write(user_id, turn)
            write_latency += ws.latency_ms
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
        rr = adapter.read(user_id, q["query"], ReadOpts(top_k=5))
        recalled_turn_ids = sorted({
            tid for m in rr.memories for tid in (m.source_turn_ids or [])
        })
        recalled_texts = [m.text for m in rr.memories]
        read_latency = rr.latency_ms
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

    # 6. 组 prompt + 调 LLM
    memory_dicts = [{"text": t} for t in recalled_texts]
    system_p, user_p = build_prompt(q["query"], memory_dicts)
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

    # 7. 判分
    jr: JudgeResult = regex_judge(answer, q)

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
        judge_score=jr.score,
        judge_verdict=jr.verdict,
        judge_positive_hits=jr.positive_hits,
        judge_negative_hits=jr.negative_hits,
        timestamp=ts,
        write_latency_ms=write_latency,
        read_latency_ms=read_latency,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="KidsBench Harness L2 主控")
    parser.add_argument("--questions", type=Path, default=Path("questions/smoke.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("--include-mem0", action="store_true", help="是否跑 Mem0Adapter（需 .venv-mem0）")
    parser.add_argument("--include-memoryos", action="store_true", help="是否跑 MemoryOSAdapter（需 .venv-memoryos）")
    parser.add_argument("--include-graphiti", action="store_true", help="是否跑 GraphitiAdapter（需 .venv-graphiti + 隧道 16379→QNAP）")
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
        "--llm-preset",
        type=str,
        default="gemini-3.5-flash",
        help="LLM preset 名（configs/llm_presets/<name>.toml）。默认 gemini-3.5-flash",
    )
    parser.add_argument(
        "--list-llm-presets",
        action="store_true",
        help="列出所有可用 preset 后退出",
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

    adapters = make_baseline_adapters()
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
    if args.include_graphiti:
        graphiti = make_graphiti_adapter(preset)
        if graphiti is None:
            print("[harness] graphiti 不可用（未装 graphiti-core 或隧道未起），跳过", flush=True)
        else:
            adapters["graphiti"] = graphiti

    print(f"[harness] adapters: {list(adapters.keys())}", flush=True)
    llm = ProxyLLMClient(preset)

    run_dir = args.out / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out_file = run_dir / "results.jsonl"

    # B1.1 第二刀：trace 启用时 monkey-patch openai + sentence_transformers
    # 让 mem0/memoryos/graphiti 内部 LLM/embedding 调用自动产生 span
    if args.trace:
        install_llm_hook()

    summary: dict[str, dict] = {}

    with out_file.open("w", encoding="utf-8") as fout:
        for adapter_name, adapter in adapters.items():
            print(f"\n=== [{adapter_name}] ===", flush=True)
            summary[adapter_name] = {"correct": 0, "wrong": 0, "evasive": 0, "error": 0, "total": 0}
            # B1: trace 启用时包 adapter，方法调用自动发 span
            traced_adapter = _trace_wrap_adapter(adapter) if args.trace else adapter
            for q in questions:
                qid = q["qid"]
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
                            exporters.append(HttpExporter(
                                endpoint_tpl=args.trace_http,
                                run_id_getter=get_current_run_id,
                            ))
                        set_exporter(MultiExporter(exporters))
                        with init_run(trace_run_id, qid=qid, adapter=adapter_name, run_group=args.run_id):
                            log = evaluate_one(traced_adapter, q, llm)
                        set_exporter(None)
                    else:
                        log = evaluate_one(traced_adapter, q, llm)
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
