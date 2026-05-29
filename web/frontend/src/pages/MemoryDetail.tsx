import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { MemorySystemMeta, StateSnapshot } from "../lib/types";

export default function MemoryDetail() {
  const { name } = useParams<{ name: string }>();
  const [meta, setMeta] = useState<MemorySystemMeta | null>(null);
  const [state, setState] = useState<StateSnapshot | null>(null);
  const [stateErr, setStateErr] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    setMeta(null);
    setState(null);
    setStateErr(null);
    api.memory(name).then(setMeta).catch((e) => setStateErr(String(e)));

    const stateFn =
      name === "mem0" ? api.stateMem0 :
      name === "memoryos" ? api.stateMemoryos :
      name === "graphiti" ? api.stateGraphiti : null;
    if (stateFn) {
      stateFn().then(setState).catch((e) => setStateErr(String(e)));
    }
  }, [name]);

  if (!meta) return <div className="text-slate-500">载入中…</div>;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">{meta.name}</h1>
        <div className="text-xs text-slate-500 mt-1">{meta.kind}</div>
      </header>

      {/* 通俗介绍 */}
      {meta.introduction && (
        <section className="card border-l-4 border-l-emerald-500">
          <h2 className="text-lg font-semibold mb-3">这是什么？</h2>

          <div className="mb-4">
            <div className="label mb-1">一句话理解</div>
            <p className="text-base text-slate-800 leading-relaxed">{meta.introduction.tldr}</p>
          </div>

          <div className="mb-4">
            <div className="label mb-1">解决什么问题</div>
            <p className="text-sm text-slate-700 leading-relaxed">{meta.introduction.problem}</p>
          </div>

          <div className="mb-4">
            <div className="label mb-2">工作机制</div>
            <ol className="text-sm text-slate-700 space-y-1.5 list-decimal list-inside">
              {meta.introduction.mechanism.map((step, i) => (
                <li key={i} className="leading-relaxed">{step}</li>
              ))}
            </ol>
          </div>

          {meta.introduction.layers && (
            <div className="mb-4">
              <div className="label mb-2">三层记忆类比</div>
              <table className="w-full text-sm">
                <thead className="text-left text-slate-500 text-xs uppercase">
                  <tr>
                    <th className="py-1.5 pr-3">层级</th>
                    <th className="py-1.5 pr-3">类比</th>
                    <th className="py-1.5 pr-3">存什么</th>
                  </tr>
                </thead>
                <tbody>
                  {meta.introduction.layers.map((l) => (
                    <tr key={l.level} className="border-t border-slate-200">
                      <td className="py-1.5 pr-3 font-medium text-slate-800">{l.level}</td>
                      <td className="py-1.5 pr-3 text-slate-600">{l.analogy}</td>
                      <td className="py-1.5 pr-3 text-slate-700">{l.content}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {meta.introduction.key_diff && (
            <div className="bg-emerald-50 border border-emerald-200 rounded p-3">
              <div className="label mb-1 text-emerald-700">关键差异</div>
              <p className="text-sm text-slate-800 leading-relaxed">{meta.introduction.key_diff}</p>
            </div>
          )}
        </section>
      )}

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">部署</h2>
        <div className="text-sm text-slate-700">{meta.deployment}</div>
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">Schema</h2>
        <pre className="font-mono text-xs text-slate-700 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(meta.schema, null, 2)}
        </pre>
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">
          状态快照
          <span className="ml-2 text-xs">
            {meta.real_time_stats ? (
              <span className="pill pill-green">实时</span>
            ) : (
              <span className="pill pill-amber">非实时</span>
            )}
          </span>
        </h2>
        <div className="text-xs text-slate-500 mb-3">来源：{meta.stats_source}</div>

        {stateErr && (
          <div className="text-rose-700 text-sm bg-rose-50 border border-rose-200 rounded p-3">
            ⚠ 状态拉取失败：{stateErr}
          </div>
        )}

        {state?.warning && (
          <div className="text-amber-700 text-sm bg-amber-50 border border-amber-200 rounded p-3 mb-3">
            ⚠ {state.warning}
          </div>
        )}

        {state && (
          <div className="space-y-3 text-sm">
            {state.graphs && (
              <div>
                <div className="label mb-1">FalkorDB 图谱列表（{state.graphs_count} 个）</div>
                <table className="w-full text-sm">
                  <thead className="text-left text-slate-500 text-xs uppercase">
                    <tr>
                      <th className="py-1 pr-3">名称</th>
                      <th className="py-1 pr-3">节点</th>
                      <th className="py-1 pr-3">边</th>
                      <th className="py-1 pr-3">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.graphs.map((g) => (
                      <tr key={g.name} className="border-t border-slate-200">
                        <td className="py-1.5 pr-3 font-mono">{g.name}</td>
                        <td className="py-1.5 pr-3">{g.nodes ?? "-"}</td>
                        <td className="py-1.5 pr-3">{g.edges ?? "-"}</td>
                        <td className="py-1.5 pr-3">
                          {g.ok ? (
                            <span className="pill pill-green">OK</span>
                          ) : (
                            <span className="pill pill-red" title={g.error}>ERR</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {state.snapshot && (
              <div>
                <div className="label mb-1">最近一次评测</div>
                <KV k="Group" v={state.snapshot.group} />
                {state.snapshot.summary_in_group && (
                  <KV
                    k="本批成绩"
                    v={`correct ${state.snapshot.summary_in_group.correct} / total ${state.snapshot.summary_in_group.total}`}
                  />
                )}
                <KV k="最近 qid" v={state.snapshot.latest_row.qid} />
                <KV k="判分" v={
                  <span className={
                    state.snapshot.latest_row.judge_verdict === "correct" ? "pill pill-green"
                    : state.snapshot.latest_row.judge_verdict === "evasive" ? "pill pill-amber"
                    : state.snapshot.latest_row.judge_verdict === "wrong" ? "pill pill-red"
                    : "pill pill-zinc"
                  }>
                    {state.snapshot.latest_row.judge_verdict}
                  </span>
                } />
                {state.snapshot.latest_row.answer && (
                  <KV k="回答" v={<span className="italic text-slate-700">"{state.snapshot.latest_row.answer}"</span>} />
                )}
              </div>
            )}

            {state.latest_run && (
              <details>
                <summary className="cursor-pointer text-slate-500 text-xs">最近一次跑 graphiti（对比当前图谱）</summary>
                <pre className="text-xs mt-2 overflow-x-auto">
                  {JSON.stringify(state.latest_run, null, 2)}
                </pre>
              </details>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 py-1 text-sm">
      <div className="text-slate-500">{k}</div>
      <div>{v}</div>
    </div>
  );
}
