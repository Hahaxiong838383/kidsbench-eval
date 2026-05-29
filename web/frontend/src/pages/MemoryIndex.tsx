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
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {Object.entries(arch.memory_systems).map(([key, m]) => (
          <Link
            key={key}
            to={`/memory/${key.replace("_storage", "")}`}
            className="card card-hover block"
          >
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
    </div>
  );
}
