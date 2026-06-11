"""Phase 1.5: reranker 中文 A/B 实测（最后一个死结）。

A 轮：默认 reranker（cross-encoder/ms-marco-MiniLM-L-6-v2，英文）
B 轮：HINDSIGHT_API_RERANKER_LOCAL_MODEL=BAAI/bge-reranker-v2-m3（中文）
跑法：RERANKER_AB_ROUND=A|B 环境变量控制；A 轮 retain 数据，B 轮复用 pg0 持久数据。
"""
from __future__ import annotations

import json
import os
import sys

ROUND = os.environ.get("RERANKER_AB_ROUND", "A")

# 1 条目标 + 4 条语义干扰（同领域不同实体，考验 rerank 区分度）
SEEDS = [
    ("t_001", "我家的布偶猫叫团子，特别喜欢吃冻干三文鱼"),
    ("t_002", "我同桌小明家养了一只金毛犬叫大黄"),
    ("t_003", "我上周在动物园看到了大熊猫吃竹子"),
    ("t_004", "妈妈说楼下王阿姨家的橘猫又胖了"),
    ("t_005", "我的美术作业画的是一只蓝色的小猫"),
]
QUERY = "我自己养的猫叫什么名字"  # 正确答案只在 t_001


def main() -> int:
    from hindsight import HindsightServer
    from hindsight_client import Hindsight

    server = HindsightServer(
        db_url="pg0",
        llm_provider="openai",
        llm_base_url="http://23.226.135.149:4000/v1",
        llm_api_key=os.environ["KIDSBENCH_GEMINI_API_KEY"],
        llm_model="gemini-3-flash-preview",
    )
    server.start(timeout=600)
    try:
        client = Hindsight(base_url=server.url)
        bank = "ab_reranker"

        if ROUND == "A":
            print(f"[{ROUND}] retain {len(SEEDS)} 条中文种子（含 4 干扰）...", flush=True)
            for tid, text in SEEDS:
                client.retain(bank_id=bank, content=text, metadata={"turn_id": tid})

        rec = client.recall(bank_id=bank, query=QUERY)
        results = rec.model_dump().get("results") or []
        print(f"\n===== ROUND {ROUND} | reranker={os.environ.get('HINDSIGHT_API_RERANKER_LOCAL_MODEL', 'DEFAULT(ms-marco英文)')} =====")
        for i, x in enumerate(results[:5]):
            tid = (x.get("metadata") or {}).get("turn_id", "?")
            print(f"  #{i+1} [{tid}] {(x.get('text') or '')[:60]}")
        top1 = (results[0].get("metadata") or {}).get("turn_id") if results else None
        print(f"RESULT_{ROUND}: top1_turn={top1} 正确={'YES' if top1 == 't_001' else 'NO'} 总召回={len(results)}")
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
