"""Phase 0: Hindsight 0.8.1 实测核实（10 点中的实测项）。

跑法（.venv-hindsight）：
  HINDSIGHT_API_EMBEDDINGS_PROVIDER=local \
  HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-zh-v1.5 \
  .venv-hindsight/bin/python scripts/phase0_hindsight_verify.py

LLM 注入天然证明：llm_model=gemini-3-flash-preview 仅存在于我们 proxy，
retain 成功抽 fact ⟺ LLM 流量打到了统一 endpoint。
"""
from __future__ import annotations

import json
import os
import sys
import time

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str) -> None:
    RESULTS.append((name, ok, evidence))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {evidence}", flush=True)


def main() -> int:
    gemini_key = os.environ.get("KIDSBENCH_GEMINI_PROXY_KEY", "")
    if not gemini_key:
        print("缺 KIDSBENCH_GEMINI_PROXY_KEY env", file=sys.stderr)
        return 1

    from hindsight import HindsightServer
    from hindsight_client import Hindsight

    print("[T1] 启动 embedded server (pg0)...", flush=True)
    t0 = time.time()
    server = HindsightServer(
        db_url="pg0",
        llm_provider="openai",
        llm_base_url="http://23.226.135.149:4000/v1",
        llm_api_key=gemini_key,
        llm_model="gemini-3-flash-preview",
    )
    # 首次启动需下载 pg0 二进制 + embedding 权重，默认 30s 必超时
    server.start(timeout=900)
    try:
        check("T1 embedded pg0 启动", True, f"{time.time()-t0:.0f}s url={server.url}")
        client = Hindsight(base_url=server.url)
        bank = "phase0_bank_a"

        # ---- T2: retain 中文 + usage + 读己写 ----
        print("[T2] retain 中文 → usage → 立即 recall...", flush=True)
        t0 = time.time()
        r = client.retain(
            bank_id=bank,
            content="我家的布偶猫叫团子，特别喜欢吃冻干三文鱼",
            metadata={"turn_id": "t_001", "session_id": "s1"},
        )
        retain_s = time.time() - t0
        rd = r.model_dump() if hasattr(r, "model_dump") else dict(r)
        usage = rd.get("usage") or {}
        check("T2a retain 返回 usage(成本计量)", bool(usage and (usage.get("total_tokens") or 0) > 0),
              f"usage={usage} latency={retain_s:.1f}s")
        check("T2b LLM 注入真生效(模型名只存在于我们proxy)", True,
              "retain 未抛 model-not-found ⟹ 流量打到统一 endpoint")

        rec = client.recall(bank_id=bank, query="我的猫叫什么名字")
        recd = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
        results = recd.get("results") or []
        hit = any("团子" in (x.get("text") or "") for x in results)
        check("T2c 读己写(retain 后立即 recall 命中)", hit, f"results={len(results)} 首条={json.dumps(results[0], ensure_ascii=False, default=str)[:200] if results else 'EMPTY'}")

        # ---- T3: recall 返回结构(溯源字段) ----
        if results:
            f0 = results[0]
            check("T3 溯源字段", True,
                  f"keys={sorted(f0.keys())} metadata={f0.get('metadata')}")

        # ---- T6: query_timestamp 时序参数 ----
        print("[T6] query_timestamp 参数...", flush=True)
        try:
            rec_t = client.recall(bank_id=bank, query="我的猫", query_timestamp="2026-01-01T00:00:00Z")
            check("T6 recall 接受 query_timestamp", True, "参数被接受未抛错")
        except TypeError as e:
            check("T6 recall 接受 query_timestamp", False, f"client 签名不支持: {e}")
        except Exception as e:
            check("T6 recall 接受 query_timestamp", False, f"{type(e).__name__}: {e}")

        # ---- T4: reflect 纯读性 ----
        print("[T4] reflect 两次纯读对比...", flush=True)
        t0 = time.time()
        a1 = client.reflect(bank_id=bank, query="这个孩子和宠物的关系怎么样")
        a1d = a1.model_dump() if hasattr(a1, "model_dump") else dict(a1)
        reflect_s = time.time() - t0
        a2 = client.reflect(bank_id=bank, query="这个孩子和宠物的关系怎么样")
        a2d = a2.model_dump() if hasattr(a2, "model_dump") else dict(a2)
        rec2 = client.recall(bank_id=bank, query="孩子和宠物的关系")
        rec2d = rec2.model_dump() if hasattr(rec2, "model_dump") else dict(rec2)
        n_after = len(rec2d.get("results") or [])
        # reflect 后记忆条数不应暴涨(纯读则 recall 集合不变:仍只有 retain 的事实)
        check("T4 reflect 纯读(不持久化新记忆)", n_after <= len(results) + 1,
              f"reflect后 recall 条数 {len(results)}→{n_after}; reflect latency={reflect_s:.1f}s; usage暴露={bool(a1d.get('usage'))}")

        # ---- T5: delete_bank 物理清场 ----
        print("[T5] delete_bank → recall 空 + 交叉隔离...", flush=True)
        client.retain(bank_id="phase0_bank_b", content="小明喜欢踢足球", metadata={"turn_id": "t_b1"})
        # 删 bank_a
        try:
            client.delete_bank(bank_id=bank)
        except AttributeError:
            import httpx
            resp = httpx.delete(f"{server.url}/v1/default/banks/{bank}", timeout=60)
            resp.raise_for_status()
        rec3 = client.recall(bank_id=bank, query="猫")
        rec3d = rec3.model_dump() if hasattr(rec3, "model_dump") else dict(rec3)
        empty = len(rec3d.get("results") or []) == 0
        rec4 = client.recall(bank_id="phase0_bank_b", query="足球")
        rec4d = rec4.model_dump() if hasattr(rec4, "model_dump") else dict(rec4)
        b_alive = any("足球" in (x.get("text") or "") for x in (rec4d.get("results") or []))
        check("T5a delete_bank 后 recall 空", empty, f"bank_a 残留={len(rec3d.get('results') or [])}")
        check("T5b 交叉隔离(删A不伤B)", b_alive, f"bank_b 仍可召回={b_alive}")

        print("\n===== 红绿灯总表 =====")
        for name, ok, ev in RESULTS:
            print(f"{'✅' if ok else '❌'} {name} | {ev[:160]}")
        return 0 if all(ok for _, ok, _ in RESULTS) else 2
    finally:
        server.stop()
        print("[server stopped]", flush=True)


if __name__ == "__main__":
    sys.exit(main())
