"""KidsBench 题库解析自测：复用 harness 真实解析路径，不调 LLM/adapter。
检查：① 加载 ② 必填字段 ③ turns/phases 解析（含 T7 noise inject 真跑）
④ build_prompt 渲染 ⑤ scene_context 红线扫描（gold 文本 4-gram 不得出现在 scene）。
"""
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, "src")
sys.path.insert(0, ".")
from harness.run_eval import (
    load_questions, _build_turn, turns_from_question, build_prompt,
)

JSONL = Path("questions/explore_v2_samples.jsonl")
QS = load_questions(JSONL)

errors, redline = [], []
tt = Counter()
n_phases = n_turns = n_scene = 0


def gold_texts(q):
    gold = set(q.get("gold_memory_ids", []))
    allt = list(q.get("turns", []))
    for ph in q.get("phases", []):
        if ph.get("phase") == "ingest":
            allt += ph.get("turns", [])
    out = []
    for t in allt:
        if t.get("turn_id") in gold:
            out.append(t.get("text") or t.get("clean_text") or "")
    return out


def cn_4grams(s):
    cn = [c for c in s if "一" <= c <= "鿿"]
    return {"".join(cn[i:i + 4]) for i in range(len(cn) - 3)}


for q in QS:
    qid = q.get("qid", "<no-qid>")
    tk = q.get("task_type", "")
    tt[tk] += 1
    try:
        if not q.get("qid") or not tk:
            errors.append(f"{qid}: 缺 qid/task_type")
            continue
        # 解析 turns 或 phases
        if "phases" in q:
            n_phases += 1
            probe = None
            has_consolidate = False
            for ph in q["phases"]:
                p = ph.get("phase")
                if p == "ingest":
                    for t in ph.get("turns", []):
                        _build_turn(t)  # 真跑（含 T7 noise inject）
                elif p == "consolidate":
                    has_consolidate = ph.get("trigger_consolidate", False)
                elif p == "probe":
                    probe = ph.get("queries", {})
            if not probe:
                errors.append(f"{qid}: phases 缺 probe.queries")
                continue
            if "control_query" in probe:  # T6
                for k in ("control_query", "control_expected", "scenario_query"):
                    if k not in probe:
                        errors.append(f"{qid}: T6 probe 缺 {k}")
                if "hypothesis" not in probe.get("control_expected", {}):
                    errors.append(f"{qid}: T6 control_expected 缺 hypothesis")
                if not q.get("expected_facts"):
                    errors.append(f"{qid}: T6 缺 expected_facts[0]（scenario 警觉命题）")
                query_for_prompt = probe["scenario_query"]
            else:  # T5
                if not probe.get("query"):
                    errors.append(f"{qid}: T5 probe 缺 query")
                query_for_prompt = probe.get("query", "")
        else:
            n_turns += 1
            turns_from_question(q)  # 真跑
            query_for_prompt = q.get("query", "")
            if not query_for_prompt:
                errors.append(f"{qid}: 缺 query")
        # build_prompt 渲染（含 scene_context）
        sc = q.get("scene_context")
        if sc:
            n_scene += 1
        build_prompt(query_for_prompt, [], sc)
        # 红线：gold 文本 4-gram 不得出现在 scene_context value
        if sc:
            scene_blob = " ".join(str(v) for v in sc.values())
            for gt in gold_texts(q):
                hit = cn_4grams(gt) & cn_4grams(scene_blob)
                if hit:
                    redline.append(f"{qid}: scene 撞 gold 片段 {sorted(hit)}")
    except Exception as e:
        errors.append(f"{qid}: 解析异常 {type(e).__name__}: {e}")

print(f"=== 加载 {len(QS)} 题 | 通用 turns 题 {n_turns} | phases 题 {n_phases} | 带 scene_context {n_scene} ===")
print("--- task_type 分布 ---")
for k in sorted(tt):
    print(f"  {k or '<空>'}: {tt[k]}")
print(f"--- 解析 error: {len(errors)} ---")
for e in errors:
    print("  ✗", e)
print(f"--- 红线泄露: {len(redline)} ---")
for r in redline:
    print("  🔴", r)
print("=== 结论:", "全部通过 ✅" if not errors and not redline else "有问题 ⚠️", "===")
