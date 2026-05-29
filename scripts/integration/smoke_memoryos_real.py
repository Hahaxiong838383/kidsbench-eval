#!/usr/bin/env python3
"""MemoryOS 真实 SDK 探路 smoke。

跑法（用 .venv-memoryos）：
    .venv-memoryos/bin/python scripts/integration/smoke_memoryos_real.py

目标：
1. 真实 Memoryos 类初始化（GEMINI_PROXY 作为 OpenAI 兼容 LLM）
2. add_memory 灌历史（user_input + agent_response 配对）
3. force_mid_term_analysis 触发 consolidate（重 LLM 调用）
4. retriever.retrieve_context 召回
5. 手动删 data_storage_path/users/{user_id} 模拟 clear
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

GEMINI_PROXY_URL = "http://23.226.135.149:4000/v1"
GEMINI_PROXY_KEY = "fq8-1NLtsbVsiJhZaISmNeobvqY0bIZMoafPnKfkuz4"

USER_ID = f"smoke_u_{int(time.time())}"
DATA_STORAGE = Path(f"/tmp/memoryos_smoke_{int(time.time())}")


def main() -> int:
    from memoryos import Memoryos

    print(f"=== MemoryOS 真实 smoke (user={USER_ID}, storage={DATA_STORAGE}) ===", flush=True)

    print("[1] 初始化 Memoryos（GEMINI_PROXY LLM + all-MiniLM-L6-v2 embedder）...", flush=True)
    try:
        mm = Memoryos(
            user_id=USER_ID,
            openai_api_key=GEMINI_PROXY_KEY,
            openai_base_url=GEMINI_PROXY_URL,
            data_storage_path=str(DATA_STORAGE),
            llm_model="gemini-3.5-flash",
            embedding_model_name="all-MiniLM-L6-v2",
            short_term_capacity=1,  # 强制每加一条 QA 立刻推到 mid_term（K12 评测需要 即时召回）
            mid_term_capacity=2000,
        )
        print(f"    ✓ Memoryos ready (data_dir={mm.user_data_dir})", flush=True)
    except Exception as e:
        print(f"    ✗ init 失败: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1

    print("\n[2] add_memory 3 个 QA pair（K12 关于团子的对话）...", flush=True)
    qa_pairs = [
        ("我家有一只猫叫团子，是布偶猫。", "团子真是一只漂亮的猫呢！布偶猫很温顺哦。"),
        ("团子最喜欢吃冻干。", "冻干是不错的零食，记得控制份量哦。"),
        ("团子最近体重三公斤了。", "三公斤的布偶猫还在生长期呢，挺正常的。"),
    ]
    for i, (ui, ar) in enumerate(qa_pairs, 1):
        try:
            t0 = time.perf_counter()
            mm.add_memory(user_input=ui, agent_response=ar)
            print(f"    ✓ pair_{i}: {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
        except Exception as e:
            print(f"    ✗ pair_{i} 失败: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return 1

    print("\n[3a] 手动 process_short_term_to_mid_term（把 short_term 3 条推到 mid_term）...", flush=True)
    try:
        t0 = time.perf_counter()
        mm.updater.process_short_term_to_mid_term()
        print(f"    ✓ short→mid done {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
    except Exception as e:
        print(f"    ✗ short→mid 失败: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1

    print("\n[3b] force_mid_term_analysis（profile + knowledge 提取，重 LLM）...", flush=True)
    try:
        t0 = time.perf_counter()
        mm.force_mid_term_analysis()
        print(f"    ✓ analysis done {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
    except Exception as e:
        print(f"    ✗ analysis 失败: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1

    print("\n[4] retriever.retrieve_context('团子是什么品种')...", flush=True)
    try:
        t0 = time.perf_counter()
        result = mm.retriever.retrieve_context(
            user_query="团子是什么品种的猫",
            user_id=USER_ID,
        )
        latency = (time.perf_counter() - t0) * 1000
        pages = result.get("retrieved_pages", [])
        u_know = result.get("retrieved_user_knowledge", [])
        a_know = result.get("retrieved_assistant_knowledge", [])
        print(f"    ✓ retrieve done {latency:.0f}ms", flush=True)
        print(f"      retrieved_pages: {len(pages)}", flush=True)
        for i, p in enumerate(pages[:3], 1):
            ui = str(p.get("user_input", "?"))[:40]
            ar = str(p.get("agent_response", "?"))[:40]
            print(f"        [{i}] user='{ui}' agent='{ar}'", flush=True)
        print(f"      user_knowledge: {len(u_know)}", flush=True)
        for k in u_know[:2]:
            print(f"        {str(k)[:80]}", flush=True)
        print(f"      assistant_knowledge: {len(a_know)}", flush=True)
    except Exception as e:
        print(f"    ✗ retrieve 失败: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1

    print("\n[5] clear 通过 shutil.rmtree(data_storage_path/users/user_id)...", flush=True)
    user_dir = DATA_STORAGE / "users" / USER_ID
    if user_dir.exists():
        shutil.rmtree(user_dir)
        print(f"    ✓ removed {user_dir}", flush=True)
    else:
        print(f"    ⚠ {user_dir} 不存在", flush=True)

    # 整体清理
    if DATA_STORAGE.exists():
        shutil.rmtree(DATA_STORAGE)

    print("\n=== ✓ MemoryOS smoke 通过 ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
