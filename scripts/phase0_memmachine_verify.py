"""MemMachine Phase 0 实测脚本（可重跑）。

前置（复跑时按此恢复）：
  1. embedding shim 在 18230（kidsbench.middleware.embedding_shim，主 .venv）
  2. server：cd /tmp/kb-phase0-memmachine &&
     MEMORY_CONFIG=/tmp/kb-phase0-memmachine/cfg.yml .venv-memmachine/bin/memmachine-server
     cfg.yml = 全 SQLite（sqlite + sqlite_vector_store/usearch）+ shim embedder + deepseek LLM
     ——零 docker / 零 Postgres / 零 Neo4j（部署重量门实测通过的关键证据）

核查点：A 中文写入→检索全链路  B metadata(turn_id) 回传溯源
        C timestamp 回填（虚拟时钟）  D 写入幂等性  E 物理清场（按 project）
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BASE = "http://127.0.0.1:8021/api/v2"
ORG, PROJ = "kidsbench", "phase0_zh"

T7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

MSGS = [
    {"content": "我家的布偶猫叫团子，特别喜欢吃冻干三文鱼。", "producer": "kid",
     "role": "user", "timestamp": T7, "metadata": {"turn_id": "t_001"}},
    {"content": "我最近在准备数学期中考试，分数应用题总是出错。", "producer": "kid",
     "role": "user", "timestamp": T7, "metadata": {"turn_id": "t_002"}},
]


def post(path: str, body: dict) -> dict:
    r = requests.post(f"{BASE}{path}", json=body, timeout=180)
    if r.status_code >= 300:
        raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def search(query: str) -> dict:
    return post("/memories/search", {"org_id": ORG, "project_id": PROJ,
                                     "query": query, "top_k": 5})


def main() -> int:
    # A+C: 中文写入（带 7 天前时间戳 + turn_id metadata）
    t0 = time.monotonic()
    res = post("/memories", {"org_id": ORG, "project_id": PROJ, "messages": MSGS})
    t_write = time.monotonic() - t0
    print(f"✅ 写入完成 {t_write:.1f}s results={json.dumps(res)[:160]}")

    # A: 中文检索
    t0 = time.monotonic()
    out = search("我养的猫叫什么名字？")
    t_read = time.monotonic() - t0
    text = json.dumps(out, ensure_ascii=False)
    print(f"--- 检索返回（{t_read:.1f}s，截断展示）---")
    print(text[:900])
    assert "团子" in text, "❌ 中文检索未命中『团子』"
    print("✅ 中文命中『团子』")

    # B: metadata / 溯源字段回传
    has_turn = "t_001" in text
    has_citation = "citation" in text or "uid" in text or "episode" in text.lower()
    print(f"溯源观察：turn_id 回传={has_turn} / 引用类字段存在={has_citation}")

    # C: timestamp 回填验证（检索结果里时间是否为注入的 7 天前）
    has_t7 = T7[:10] in text
    print(f"时间戳观察：注入日期 {T7[:10]} 出现在结果中={has_t7}")

    # D: 幂等性——同内容再写一遍，看检索结果是否翻倍
    post("/memories", {"org_id": ORG, "project_id": PROJ, "messages": [MSGS[0]]})
    out2 = search("团子喜欢吃什么？")
    text2 = json.dumps(out2, ensure_ascii=False)
    dup_count = text2.count("冻干三文鱼")
    print(f"幂等观察：重复写入后『冻干三文鱼』出现 {dup_count} 次（>1 即非幂等，adapter 需写前查重）")

    # E: 物理清场（删整个 project）
    post("/projects/delete", {"org_id": ORG, "project_id": PROJ})
    out3 = search("团子")
    text3 = json.dumps(out3, ensure_ascii=False)
    if "团子" in text3:
        print("❌ 清场后仍能检索到『团子』——非物理清除")
        return 1
    print("✅ 清场后检索归零")

    print("\n=== MemMachine 实测：核心链路全通（全 SQLite 零外部服务）===")
    print(f"write={t_write:.1f}s read={t_read:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
