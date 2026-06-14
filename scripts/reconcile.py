import json, sys
# 用法: reconcile.py <air_results.jsonl> <dev_results.jsonl> [label]
# 分类判据(2026-06-14 川哥定):
#   确定性系统(memmachine/cognee 等存原文/图,检索确定)→ recall dict 必须精确 100% 一致
#   非确定性系统(mem0/memoryos/reme/hindsight 等 LLM 抽取记忆)→ 抽取非确定性致 extras 每次不同,
#     改判聚合: |recall 均值差|<0.05 且 准确率 dev 不低于 Air-0.05
DETERMINISTIC = {"memmachine", "cognee"}  # 按 adapter 名前缀判定
DELTA_GATE = 0.05

air_path, dev_path = sys.argv[1], sys.argv[2]
label = sys.argv[3] if len(sys.argv) > 3 else ""

def rget(r, *ks):
    for k in ks:
        if k in r and r[k] is not None: return r[k]
    return None
def load(p):
    d = {}
    for line in open(p):
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        ad = rget(r, "adapter", "system") or "?"
        d[(r["qid"], ad)] = r
    return d
def recall_val(r):
    rm = rget(r, "recall_metric")
    if isinstance(rm, dict): return rm.get("recall")
    return None
def is_det(adapter):
    return any(adapter == s or adapter.startswith(s) for s in DETERMINISTIC)

A = load(air_path); D = load(dev_path)
adapters = sorted({k[1] for k in A} | {k[1] for k in D})
print(f"=== {label} === Air行={len(A)} dev198行={len(D)} adapters={adapters}")
overall_ok = True
for ad in adapters:
    ak = {k for k in A if k[1] == ad}; dk = {k for k in D if k[1] == ad}
    common = sorted(ak & dk)
    n = len(common)
    if n == 0:
        print(f"  [{ad}] 无共有 qid (仅Air {len(ak)} / 仅dev {len(dk)})")
        continue
    det = is_det(ad)
    # 通用统计
    rec_a = [recall_val(A[q]) for q in common]; rec_d = [recall_val(D[q]) for q in common]
    pair = [(a, b) for a, b in zip(rec_a, rec_d) if a is not None and b is not None]
    mean_a = sum(a for a, _ in pair)/len(pair) if pair else 0
    mean_d = sum(b for _, b in pair)/len(pair) if pair else 0
    delta = mean_d - mean_a
    val_match = sum(1 for a, b in pair if abs(a - b) < 1e-9)
    dict_exact = sum(1 for q in common if rget(A[q], "recall_metric") == rget(D[q], "recall_metric"))
    acc_a = sum(1 for q in common if rget(A[q], "judge_verdict") == "correct")/n
    acc_d = sum(1 for q in common if rget(D[q], "judge_verdict") == "correct")/n
    vagree = sum(1 for q in common if rget(A[q], "judge_verdict") == rget(D[q], "judge_verdict"))
    miss = len(dk - ak); lack = len(ak - dk)

    if det:
        ok = (dict_exact == n)
        flag = "✅" if ok else "❌"
        print(f"  [{ad}|确定性] n={n} | recall dict精确 {dict_exact}/{n}={100*dict_exact/n:.1f}% {flag} | verdict一致 {vagree}/{n}={100*vagree/n:.1f}% | Air多{lack} dev多{miss}")
        if not ok:
            overall_ok = False
            for q in common:
                if rget(A[q], "recall_metric") != rget(D[q], "recall_metric"):
                    print(f"      dict差 {q[0]}: Air={rget(A[q],'recall_metric')} dev={rget(D[q],'recall_metric')}")
    else:
        ok = (abs(delta) < DELTA_GATE) and (acc_d >= acc_a - DELTA_GATE)
        flag = "✅" if ok else "❌"
        print(f"  [{ad}|非确定] n={n} | recall均值 Air={mean_a:.3f} dev={mean_d:.3f} Δ={delta:+.3f} {'OK' if abs(delta)<DELTA_GATE else 'GATE!'} "
              f"| 准确率 Air={acc_a:.3f} dev={acc_d:.3f} Δ={acc_d-acc_a:+.3f} {'OK' if acc_d>=acc_a-DELTA_GATE else 'GATE!'} {flag}")
        print(f"       (参考: recall值逐题一致 {val_match}/{len(pair)}={100*val_match/len(pair):.1f}% | verdict一致 {vagree}/{n}={100*vagree/n:.1f}% | Air多{lack} dev多{miss})")
        if not ok: overall_ok = False
print(f"  => {label} {'通过 ✅' if overall_ok else '未通过/不完整 ❌'}")
