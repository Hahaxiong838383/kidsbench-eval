import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { ArchitectureIndex } from "../lib/types";

export default function AdaptersIndex() {
  const [arch, setArch] = useState<ArchitectureIndex | null>(null);
  useEffect(() => {
    api.architecture().then(setArch);
  }, []);
  if (!arch) return <div className="text-slate-500">载入中…</div>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Adapter 列表</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {Object.entries(arch.adapters).map(([key, a]) => (
          <Link key={key} to={`/adapters/${key}`} className="card card-hover block">
            <div className="text-xl font-semibold">{a.name}</div>
            <div className="text-xs text-slate-500 mt-1">
              {a.sdk.package} v{a.sdk.version}
            </div>
            <div className="mt-3 text-sm">{a.storage}</div>
            <ul className="text-xs text-slate-600 mt-2 space-y-0.5">
              {a.methods.map((m) => (
                <li key={m.name}>· {m.name}</li>
              ))}
            </ul>
          </Link>
        ))}
      </div>
    </div>
  );
}
