import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useSource } from "../lib/sourceContext";
import type { ArchitectureIndex, RunGroup } from "../lib/types";

export default function Home() {
  const { source } = useSource();
  const [arch, setArch] = useState<ArchitectureIndex | null>(null);
  const [latestGroups, setLatestGroups] = useState<RunGroup[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.architecture(), api.runGroups({ source })])
      .then(([a, g]) => {
        setArch(a);
        setLatestGroups(g.items.slice(0, 5));
      })
      .catch((e) => setErr(String(e)));
  }, [source]);

  if (err) return <div className="text-red-400">载入失败：{err}</div>;
  if (!arch) return <div className="text-slate-500">载入中…</div>;

  return (
    <div className="space-y-8">
      {/* 顶部 banner */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Stat label="Adapter" value={Object.keys(arch.adapters).length} />
        <Stat label="记忆系统" value={Object.keys(arch.memory_systems).length} />
        <Stat label="历史 Run Groups" value={latestGroups.length} />
        <Stat
          label="Embedding 模型"
          value={arch.embedding_model.name.split("/").pop() ?? ""}
          sub={`${arch.embedding_model.dim}d · ${arch.embedding_model.size_mb}MB`}
        />
      </div>

      {/* 架构总览（CSS Grid 简化版数据流图）*/}
      <section>
        <h2 className="text-lg font-semibold mb-3">架构总览</h2>
        <div className="card">
          <DataFlowDiagram adapters={Object.keys(arch.adapters)} />
        </div>
      </section>

      {/* 3 个 adapter 卡片 */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Adapter（点击进详情）</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {Object.entries(arch.adapters).map(([key, a]) => (
            <Link
              to={`/adapters/${key}`}
              key={key}
              className="card card-hover block"
            >
              <div className="text-xl font-semibold">{a.name}</div>
              <div className="text-xs text-slate-500 mt-1">
                {a.sdk.package} · v{a.sdk.version}
              </div>
              <div className="mt-3 text-sm text-slate-600">{a.storage}</div>
              <div className="mt-2 flex flex-wrap gap-1">
                {a.methods.map((m) => (
                  <span key={m.name} className="pill pill-zinc">
                    {m.name}
                  </span>
                ))}
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* 3 个记忆系统卡片（含 TL;DR） */}
      <section>
        <h2 className="text-lg font-semibold mb-3">记忆系统</h2>
        <p className="text-sm text-slate-500 mb-3">
          3 套不同范式：向量库 / 分层 / 知识图谱。每张卡片里是一句话理解，点开看完整介绍。
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {Object.entries(arch.memory_systems).map(([key, m]) => (
            <Link
              to={`/memory/${key.replace("_storage", "")}`}
              key={key}
              className="card card-hover block"
            >
              <div className="flex items-baseline justify-between gap-2">
                <div className="text-base font-semibold">{m.name}</div>
                <span className="pill pill-zinc text-[10px]">{m.kind}</span>
              </div>
              {m.introduction?.tldr && (
                <p className="mt-3 text-sm text-slate-700 leading-relaxed">
                  {m.introduction.tldr}
                </p>
              )}
              <div className="mt-3 pt-3 border-t border-slate-100 text-xs text-slate-500">
                {m.deployment}
              </div>
              <div className="mt-2">
                {m.real_time_stats ? (
                  <span className="pill pill-green">实时状态</span>
                ) : (
                  <span className="pill pill-amber">非实时（从 run 拉）</span>
                )}
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* 最近 run 摘要 */}
      <section>
        <h2 className="text-lg font-semibold mb-3">最近 Run Groups</h2>
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500 text-xs uppercase">
              <tr>
                <th className="py-2 pr-4">Group</th>
                <th className="py-2 pr-4">目标 Adapter</th>
                <th className="py-2 pr-4">时代</th>
                <th className="py-2 pr-4">题数</th>
                <th className="py-2 pr-4">Summary</th>
              </tr>
            </thead>
            <tbody>
              {latestGroups.map((g) => (
                <tr
                  key={g.name}
                  className="border-t border-slate-200 hover:bg-slate-50"
                >
                  <td className="py-2 pr-4 font-mono">{g.name}</td>
                  <td className="py-2 pr-4">{g.target_adapter}</td>
                  <td className="py-2 pr-4">
                    <EraPill era={g.era} />
                  </td>
                  <td className="py-2 pr-4">{g.items_count}</td>
                  <td className="py-2 pr-4 text-xs">
                    {g.summary &&
                      Object.entries(g.summary)
                        .map(
                          ([adp, s]) =>
                            `${adp}: ${s.correct}/${s.total}`,
                        )
                        .join("  ·  ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </div>
  );
}

function EraPill({ era }: { era: string }) {
  const cls =
    era === "after_bge" ? "pill pill-green"
    : era === "before_bge" ? "pill pill-amber"
    : "pill pill-zinc";
  return <span className={cls}>{era}</span>;
}

function DataFlowDiagram({ adapters }: { adapters: string[] }) {
  return (
    <div className="font-mono text-xs space-y-2 leading-relaxed text-slate-700">
      <div>题库（questions/smoke.jsonl）</div>
      <div className="text-slate-400">         ↓</div>
      <div>harness/run_eval.py</div>
      <div className="text-slate-400">         ↓        ↓        ↓</div>
      <div className="grid grid-cols-3 gap-2 mt-1">
        {adapters.map((a) => (
          <div key={a} className="card !p-3 !bg-white">
            <div className="text-emerald-600">{a}</div>
            <div className="text-slate-400 text-[10px] mt-1">
              {a === "mem0" ? "→ Qdrant" : a === "memoryos" ? "→ 三层 + faiss" : "→ FalkorDB 图谱"}
            </div>
          </div>
        ))}
      </div>
      <div className="text-slate-400 mt-2">
        中间件：EmbeddingService (bge-small-zh-v1.5, 512d) + GEMINI_PROXY (gemini-3.5-flash)
      </div>
    </div>
  );
}
