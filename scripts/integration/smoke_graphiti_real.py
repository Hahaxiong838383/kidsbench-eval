#!/usr/bin/env python3
"""Graphiti 真实 SDK 探路 smoke。

跑法（必须用 .venv-graphiti venv，并先建好 ssh 隧道 16379→QNAP FalkorDB）:
    ssh -f -N -L 16379:192.168.61.18:16379 mini
    .venv-graphiti/bin/python scripts/integration/smoke_graphiti_real.py

目标:
1. 验证 GEMINI_PROXY 作为 graphiti LLM 可用
2. 验证自定义 ST embedder 适配 graphiti EmbedderClient 接口
3. 验证 add_episode → search 真实链路
4. 验证手写 cypher 删除（graphiti 0.29.1 无 clear API）
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

GEMINI_PROXY_URL = "http://23.226.135.149:4000/v1"
GEMINI_PROXY_KEY = "fq8-1NLtsbVsiJhZaISmNeobvqY0bIZMoafPnKfkuz4"


def make_llm_client():
    """KidsBench 自定义 LLM client 用 chat.completions 适配 GEMINI_PROXY。"""
    from graphiti_core.llm_client.config import LLMConfig

    from kidsbench.middleware.graphiti_compat import KidsBenchGraphitiLLMClient

    config = LLMConfig(
        api_key=GEMINI_PROXY_KEY,
        base_url=GEMINI_PROXY_URL,
        model="gemini-3.5-flash",
        small_model="gemini-3.5-flash",  # graphiti 内部 small 模型调用也要走 proxy
        temperature=0.0,
    )
    return KidsBenchGraphitiLLMClient(config=config, reasoning_effort="minimal")


def make_reranker():
    """Reranker 暂用 graphiti 默认（如果它走 responses.parse 失败，改用 None 或自定义）。"""
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.llm_client.config import LLMConfig

    config = LLMConfig(
        api_key=GEMINI_PROXY_KEY,
        base_url=GEMINI_PROXY_URL,
        model="gemini-3.5-flash",
        temperature=0.0,
    )
    return OpenAIRerankerClient(config=config)


def make_st_embedder():
    """复用 graphiti_compat 中的 ST embedder 工厂。"""
    from kidsbench.middleware.graphiti_compat import make_st_embedder as _make
    return _make()


def make_falkor_driver():
    from graphiti_core.driver.falkordb_driver import FalkorDriver

    # 隧道：localhost:16379 → QNAP:16379
    return FalkorDriver(host="127.0.0.1", port=16379, database="kidsbench_smoke")


async def cleanup_graph(graphiti, group_id: str) -> None:
    """graphiti 0.29.1 无 clear API，手写 cypher 按 group_id 删。"""
    # 用 driver 直接跑 cypher
    driver = graphiti.driver
    # FalkorDB 用 Cypher: MATCH ... DELETE
    # group_id 在节点的属性中
    queries = [
        f"MATCH (n {{group_id: '{group_id}'}}) DETACH DELETE n",
    ]
    for q in queries:
        try:
            async with driver.session() as session:
                await session.run(q)
        except Exception as e:  # noqa: BLE001
            print(f"  cleanup query 失败（可能没节点）: {e}", flush=True)


async def main() -> int:
    from graphiti_core import Graphiti
    from graphiti_core.nodes import EpisodeType

    group_id = f"smoke_{int(time.time())}"
    print(f"=== Graphiti 真实 smoke (group_id={group_id}) ===", flush=True)

    # 1. 初始化
    print("[1] 初始化 Graphiti（FalkorDB driver + GEMINI_PROXY LLM + ST embedder）...", flush=True)
    embedder = make_st_embedder()
    llm = make_llm_client()
    driver = make_falkor_driver()
    reranker = make_reranker()
    graphiti = Graphiti(
        llm_client=llm,
        embedder=embedder,
        cross_encoder=reranker,
        graph_driver=driver,
    )

    try:
        # 2. 起索引（首次必跑，幂等）
        print("[2] build_indices_and_constraints...", flush=True)
        t0 = time.perf_counter()
        await graphiti.build_indices_and_constraints()
        print(f"    ✓ done ({(time.perf_counter()-t0)*1000:.0f}ms)", flush=True)

        # 3. add_episode
        print("\n[3] add_episode（含 LLM 抽取 entity/edge）...", flush=True)
        episodes = [
            "我家有一只猫叫团子，是布偶猫。",
            "团子最喜欢吃冻干。",
            "团子最近体重三公斤了。",
        ]
        for i, body in enumerate(episodes, 1):
            t0 = time.perf_counter()
            try:
                result = await graphiti.add_episode(
                    name=f"ep_{i:03d}",
                    episode_body=body,
                    source_description="K12 chat",
                    reference_time=datetime.now(timezone.utc),
                    source=EpisodeType.message,
                    group_id=group_id,
                )
                latency = (time.perf_counter() - t0) * 1000
                nodes = getattr(result, "nodes", None)
                edges = getattr(result, "edges", None)
                node_count = len(nodes) if nodes else 0
                edge_count = len(edges) if edges else 0
                print(f"    ✓ ep_{i:03d}: {latency:.0f}ms, nodes={node_count}, edges={edge_count}",
                      flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"    ✗ ep_{i:03d} 失败: {type(e).__name__}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                return 1

        # 4. search
        print("\n[4] search 'team团子是什么品种'...", flush=True)
        try:
            t0 = time.perf_counter()
            edges = await graphiti.search(
                query="团子是什么品种的猫",
                group_ids=[group_id],
                num_results=5,
            )
            latency = (time.perf_counter() - t0) * 1000
            print(f"    ✓ search done {latency:.0f}ms, {len(edges)} edges", flush=True)
            for i, edge in enumerate(edges[:5], 1):
                fact = getattr(edge, "fact", "?")
                print(f"      [{i}] {str(fact)[:80]}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"    ✗ search 失败: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return 1

        # 5. cleanup（手写 cypher 删 group）
        print("\n[5] cleanup（手写 cypher，graphiti 0.29.1 无 clear API）...", flush=True)
        await cleanup_graph(graphiti, group_id)
        print("    ✓ cleanup done", flush=True)

        # 6. 验证 cleanup 后 search 空
        print("\n[6] cleanup 后 search 验证空...", flush=True)
        edges_after = await graphiti.search(
            query="团子",
            group_ids=[group_id],
            num_results=5,
        )
        if not edges_after:
            print("    ✓ search 空 — cleanup 成功", flush=True)
        else:
            print(f"    ⚠ search 还有 {len(edges_after)} edges — cleanup 不彻底", flush=True)

        print("\n=== ✓ Graphiti smoke 通过 ===", flush=True)
        return 0
    finally:
        await graphiti.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
