import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { ArchitectureIndex } from "../lib/types";

export default function MemoryIndex() {
  const [arch, setArch] = useState<ArchitectureIndex | null>(null);
  useEffect(() => {
    api.architecture().then(setArch);
  }, []);
  if (!arch) return <div className="text-slate-500">载入中…</div>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">记忆系统</h1>

      <h2 className="text-lg font-semibold mt-2">真实记忆系统</h2>
      <p className="text-sm text-slate-500">实际接入评测的记忆系统。点进去看机制白盒。</p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {Object.entries(arch.memory_systems)
          .filter(([, m]) => !(m as { is_baseline?: boolean }).is_baseline)
          .map(([key, m]) => (
          <Link key={key} to={`/memory/${key.replace("_storage", "")}`} className="card card-hover block">
            <div className="text-base font-semibold">{m.name}</div>
            <div className="text-xs text-slate-500 mt-1">{m.kind}</div>
            <div className="mt-3 text-sm">{m.deployment}</div>
            <div className="mt-2">
              {m.real_time_stats ? (
                <span className="pill pill-green">实时状态</span>
              ) : (
                <span className="pill pill-amber">非实时</span>
              )}
            </div>
          </Link>
        ))}
      </div>

      <h2 className="text-lg font-semibold mt-6">参照基线（不是真实系统，是评测的科学对照组）</h2>
      <p className="text-sm text-slate-500">
        榜单上和真实系统一起出现，但它们是『尺子』不是『产品』——用来校准题目和判分。
        点进去看每个的作用和为什么这么设计。
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {Object.entries(arch.memory_systems)
          .filter(([, m]) => (m as { is_baseline?: boolean }).is_baseline)
          .map(([key, m]) => (
          <Link key={key} to={`/memory/${key.replace("_storage", "")}`}
            className="card card-hover block border-amber-200 bg-amber-50/40">
            <div className="text-base font-semibold">{m.name}</div>
            <div className="text-xs text-amber-700 mt-1 font-medium">⚖️ {m.kind}</div>
            <div className="mt-3 text-sm text-slate-600">{m.deployment}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
