import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { RunGroup } from "../lib/types";

export default function Runs() {
  const [groups, setGroups] = useState<RunGroup[]>([]);
  const [adapterFilter, setAdapterFilter] = useState("");
  const [eraFilter, setEraFilter] = useState("");

  useEffect(() => {
    api
      .runGroups({
        adapter: adapterFilter || undefined,
        era: eraFilter || undefined,
      })
      .then((r) => setGroups(r.items));
  }, [adapterFilter, eraFilter]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">历史 Run Groups</h1>
      <p className="text-sm text-slate-500">
        一个 group 对应一次完整批跑（同一份题库 × 多个 adapter）。点开看每行 result。
      </p>

      {/* 筛选 */}
      <div className="flex gap-3 items-center">
        <label className="text-xs text-slate-500">目标 Adapter</label>
        <select
          value={adapterFilter}
          onChange={(e) => setAdapterFilter(e.target.value)}
          className="bg-white border border-slate-200 rounded px-2 py-1 text-sm"
        >
          <option value="">全部</option>
          <option value="mem0">mem0</option>
          <option value="memoryos">memoryos</option>
          <option value="graphiti">graphiti</option>
          <option value="baseline">baseline</option>
        </select>
        <label className="text-xs text-slate-500 ml-3">时代</label>
        <select
          value={eraFilter}
          onChange={(e) => setEraFilter(e.target.value)}
          className="bg-white border border-slate-200 rounded px-2 py-1 text-sm"
        >
          <option value="">全部</option>
          <option value="after_bge">after_bge</option>
          <option value="before_bge">before_bge</option>
          <option value="baseline">baseline</option>
        </select>
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500 text-xs uppercase">
            <tr>
              <th className="py-2 pr-4">Group</th>
              <th className="py-2 pr-4">目标 Adapter</th>
              <th className="py-2 pr-4">时代</th>
              <th className="py-2 pr-4">题数</th>
              <th className="py-2 pr-4">成绩矩阵</th>
              <th className="py-2 pr-4">时间</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.name} className="border-t border-slate-200 hover:bg-slate-50">
                <td className="py-2 pr-4 font-mono">{g.name}</td>
                <td className="py-2 pr-4">{g.target_adapter}</td>
                <td className="py-2 pr-4">
                  <span
                    className={
                      g.era === "after_bge" ? "pill pill-green"
                      : g.era === "before_bge" ? "pill pill-amber"
                      : "pill pill-zinc"
                    }
                  >
                    {g.era}
                  </span>
                </td>
                <td className="py-2 pr-4">{g.items_count}</td>
                <td className="py-2 pr-4">
                  {g.summary && (
                    <div className="flex gap-2 flex-wrap">
                      {Object.entries(g.summary).map(([adp, s]) => (
                        <span
                          key={adp}
                          className={
                            s.correct === s.total ? "pill pill-green"
                            : s.correct > 0 ? "pill pill-amber"
                            : "pill pill-red"
                          }
                          title={`correct ${s.correct} / wrong ${s.wrong} / evasive ${s.evasive} / err ${s.error}`}
                        >
                          {adp} {s.correct}/{s.total}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="py-2 pr-4 text-xs text-slate-500">
                  {new Date(g.mtime * 1000).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
