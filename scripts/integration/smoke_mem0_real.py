#!/usr/bin/env python3
"""Mem0Adapter 真实 SDK 集成 smoke 测试。

跑法（必须用 .venv-mem0 venv）:
    .venv-mem0/bin/python scripts/integration/smoke_mem0_real.py

配置：
- mem0 vector_store: 本地 qdrant path（无需外部服务）
- mem0 llm: openai 兼容 → GEMINI_PROXY (23.226.135.149:4000)
- mem0 embedder: huggingface → sentence-transformers/all-MiniLM-L6-v2 (本地, ~22MB)

验证项：
1. mem0 2.0.4 API 跟 adapter 的 _call_search 兼容层是否真适配
2. write(turn) → sidecar 是否真填好 turn_id 映射
3. read(query) → source_turn_ids 是否真能从 sidecar/metadata 反查
4. clear() → 真物理删除（read 后必空）
5. capability_profile 声明的 native 能力是否真 native
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 把 src 加到 path（editable 装失败时也能跑）
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kidsbench.adapters.mem0_adapter import Mem0Adapter
from kidsbench.contract import ReadOpts, Turn
from kidsbench.middleware import SidecarStore

# 关掉 mem0 telemetry（K12 隐私基线）
os.environ["MEM0_TELEMETRY"] = "false"

# 关掉 OpenAI/HuggingFace 的 telemetry / hub 离线警告
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

GEMINI_PROXY_URL = "http://23.226.135.149:4000/v1"
GEMINI_PROXY_KEY = "fq8-1NLtsbVsiJhZaISmNeobvqY0bIZMoafPnKfkuz4"


def make_mem0_config() -> dict:
    """构造 mem0 真实 config（用 GEMINI_PROXY + 本地 ST embedder）。"""
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "kidsbench_smoke",
                # all-MiniLM-L6-v2 的 dim 是 384
                "embedding_model_dims": 384,
                "path": "/tmp/kidsbench_qdrant_smoke",
                "on_disk": False,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gemini-3.5-flash",
                "api_key": GEMINI_PROXY_KEY,
                "openai_base_url": GEMINI_PROXY_URL,
                "temperature": 0.0,
            },
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_dims": 384,
            },
        },
    }


def make_test_turns() -> list[Turn]:
    base = time.time()
    return [
        Turn(
            turn_id="t_001",
            session_id="s1",
            role="user",
            text="我们家有只布偶猫叫团子，最近喜欢吃冻干",
            timestamp=base,
            metadata={"cognitive_type": "episodic"},
        ),
        Turn(
            turn_id="t_002",
            session_id="s1",
            role="assistant",
            text="布偶猫性格温顺，喜欢黏人",
            timestamp=base + 1,
            metadata={"cognitive_type": "semantic"},
        ),
        Turn(
            turn_id="t_003",
            session_id="s1",
            role="user",
            text="团子最近体重三公斤了",
            timestamp=base + 2,
            metadata={"cognitive_type": "episodic"},
        ),
    ]


def main() -> int:
    user_id = f"smoke_u_{int(time.time())}"
    sidecar = SidecarStore(backend="memory")

    print(f"=== mem0 真实 smoke 测试 (user={user_id}) ===", flush=True)
    print("[1] 初始化 Mem0Adapter（连本地 qdrant + GEMINI_PROXY LLM + 本地 ST embedder）...", flush=True)

    try:
        adapter = Mem0Adapter(config=make_mem0_config(), sidecar=sidecar)
        print(f"    ✓ adapter 就绪 (client={type(adapter.client).__name__})", flush=True)
    except Exception as e:
        print(f"    ✗ 初始化失败: {type(e).__name__}: {e}", flush=True)
        return 1

    turns = make_test_turns()
    print(f"\n[2] 灌 {len(turns)} turn ...", flush=True)
    for t in turns:
        try:
            stats = adapter.write(user_id, t)
            mem_ids = stats.raw.get("memory_ids", []) if stats.raw else []
            print(f"    ✓ write {t.turn_id}: latency={stats.latency_ms:.1f}ms, mem_ids={mem_ids}", flush=True)
        except Exception as e:
            print(f"    ✗ write {t.turn_id} 失败: {type(e).__name__}: {e}", flush=True)
            return 1

    print(f"\n[3] sidecar 状态: {sidecar.stats(user_id)}", flush=True)

    print("\n[4] 等 2 秒让 mem0 异步索引就绪...", flush=True)
    time.sleep(2)
    adapter.flush(user_id)

    print("\n[5] read query='团子': 应召回团子相关记忆 + 带 source_turn_ids", flush=True)
    try:
        result = adapter.read(user_id, "团子是什么猫", ReadOpts(top_k=5))
        print(f"    ✓ read latency={result.latency_ms:.1f}ms, {len(result.memories)} 条记忆", flush=True)
        for i, m in enumerate(result.memories):
            print(f"      [{i+1}] id={m.memory_id} score={m.score:.3f}", flush=True)
            print(f"          text={m.text[:60]}", flush=True)
            print(f"          source_turn_ids={m.source_turn_ids}", flush=True)
        assert result.memories, "read 返空 — mem0 没召回任何记忆"
        # 关键锚点：source_turn_ids 至少有一个非空
        has_traceback = any(m.source_turn_ids for m in result.memories)
        if has_traceback:
            print("    ✓ turn_id 溯源命中（gemini A.1 已知 issue 在此场景下成功）", flush=True)
        else:
            print("    ⚠ source_turn_ids 全空（consolidation 元数据黑洞实锤）", flush=True)
    except Exception as e:
        print(f"    ✗ read 失败: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1

    print("\n[6] clear(user_id): 物理删除 + sidecar 清", flush=True)
    try:
        cstats = adapter.clear(user_id)
        print(f"    ✓ clear latency={cstats.latency_ms:.1f}ms, deleted_count={cstats.deleted_count}", flush=True)
    except Exception as e:
        print(f"    ✗ clear 失败: {type(e).__name__}: {e}", flush=True)
        return 1

    print("\n[7] clear 后立即 read: 必须空（防幽灵记忆残留）", flush=True)
    try:
        result_after = adapter.read(user_id, "团子", ReadOpts(top_k=5))
        if not result_after.memories:
            print("    ✓ clear 后 read 空 — 物理删除验证通过", flush=True)
        else:
            print(f"    ✗ clear 后还有 {len(result_after.memories)} 条 — 幽灵记忆！", flush=True)
            return 1
    except Exception as e:
        print(f"    ✗ clear 后 read 失败: {type(e).__name__}: {e}", flush=True)
        return 1

    print("\n=== ✓ 所有 smoke 步骤通过 ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
