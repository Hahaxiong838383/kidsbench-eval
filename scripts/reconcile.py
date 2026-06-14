import json, sys
# 用法: reconcile.py <air_results.jsonl> <dev_results.jsonl> [label]
air_path, dev_path = sys.argv[1], sys.argv[2]
label = sys.argv[3] if len(sys.argv)>3 else ""
def rget(r,*ks):
    for k in ks:
        if k in r and r[k] is not None: return r[k]
    return None
def load(p):
    d={}
    for line in open(p):
        line=line.strip()
        if not line: continue
        r=json.loads(line)
        ad=rget(r,"adapter","system") or "?"
        d[(r["qid"],ad)]=r
    return d
A=load(air_path); D=load(dev_path)
# 按 adapter 分组共有键
adapters=sorted({k[1] for k in A} | {k[1] for k in D})
print(f"=== {label} === Air行={len(A)} dev198行={len(D)} adapters={adapters}")
overall_ok=True
for ad in adapters:
    ak={k for k in A if k[1]==ad}; dk={k for k in D if k[1]==ad}
    common=sorted(ak & dk)
    n=len(common)
    if n==0:
        print(f"  [{ad}] 无共有 qid (仅Air {len(ak)} / 仅dev {len(dk)})"); overall_ok=False; continue
    rm=sum(1 for q in common if rget(A[q],"recall_metric")==rget(D[q],"recall_metric"))
    vm=sum(1 for q in common if rget(A[q],"judge_verdict")==rget(D[q],"judge_verdict"))
    sd=sum(abs((rget(A[q],"judge_score") or 0)-(rget(D[q],"judge_score") or 0)) for q in common)/n
    rec_pct=100*rm/n; ver_pct=100*vm/n
    flag = "✅" if rm==n else ("⚠️" if rec_pct>=98 else "❌")
    print(f"  [{ad}] n={n} | recall一致 {rm}/{n}={rec_pct:.1f}% {flag} | verdict一致 {vm}/{n}={ver_pct:.1f}% | score差{sd:.4f} | Air缺{len(dk-ak)} dev缺{len(ak-dk)}")
    if rm<n:
        overall_ok=False
        diffs=[(q[0],rget(A[q],'recall_metric'),rget(D[q],'recall_metric')) for q in common if rget(A[q],'recall_metric')!=rget(D[q],'recall_metric')]
        for q,a,b in diffs[:6]: print(f"      recall差 {q}: Air={a} dev={b}")
print(f"  => recall {'全档100%一致' if overall_ok else '存在不一致(见上)'}")
