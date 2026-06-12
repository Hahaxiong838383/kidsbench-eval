import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ArchitectureIndex } from "../lib/types";

export default function System() {
  const [health, setHealth] = useState<{ version: string; phase: string } | null>(null);
  const [arch, setArch] = useState<ArchitectureIndex | null>(null);

  useEffect(() => {
    api.health().then(setHealth);
    api.architecture().then(setArch);
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">系统状态</h1>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">后端</h2>
        <KV k="版本" v={health?.version ?? "..."} />
        <KV k="阶段" v={health?.phase ?? "..."} />
        <KV k="后端实现" v="FastAPI + uvicorn (Python 3.12)" />
        <KV k="测试" v="16 单元测试全过" />
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">模型配置</h2>
        {arch && (
          <>
            <KV k="LLM" v={`${arch.llm_model.name} · ${arch.llm_model.provider}`} />
            <KV k="LLM endpoint" v={<code className="font-mono text-xs">{arch.llm_model.endpoint}</code>} />
            <KV k="LLM reasoning" v={arch.llm_model.reasoning_effort} />
            <KV k="Embedding" v={`${arch.embedding_model.name} · ${arch.embedding_model.dim}d · ${arch.embedding_model.size_mb}MB`} />
            <KV k="非对称模型" v={arch.embedding_model.is_asymmetric ? "是（Query 需 instruction，B0 未加，见 EMBEDDING_KNOWN_ISSUES.md）" : "否"} />
          </>
        )}
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">AI 助手管理</h2>
        <p className="text-sm text-slate-600 mb-3">
          管理 AI 助手的手机号白名单、每号配额、用量看板和全局设置（需管理密码登录）。
        </p>
        <a
          href="/admin"
          className="inline-block px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700"
        >
          进入管理后台 →
        </a>
        <p className="text-xs text-slate-400 mt-2">
          助手机制白盒说明见右下角助手抽屉，或 GET /api/assistant/info
        </p>
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold mb-3">文档链接</h2>
        <ul className="space-y-2 text-sm">
          <li>
            <span className="text-slate-500">SPEC：</span>
            <code className="font-mono text-emerald-600 text-xs">docs/WEB_PLATFORM_SPEC.md</code>
          </li>
          <li>
            <span className="text-slate-500">B0 Plan：</span>
            <code className="font-mono text-emerald-600 text-xs">docs/WEB_PLATFORM_PHASE_B0.md</code>
          </li>
          <li>
            <span className="text-slate-500">已知问题：</span>
            <code className="font-mono text-emerald-600 text-xs">docs/EMBEDDING_KNOWN_ISSUES.md</code>
          </li>
          <li>
            <span className="text-slate-500">Adapter 指南：</span>
            <code className="font-mono text-emerald-600 text-xs">docs/ADAPTER_GUIDE.md</code>
          </li>
          <li>
            <span className="text-slate-500">Backend README：</span>
            <code className="font-mono text-emerald-600 text-xs">web/backend/README.md</code>
          </li>
        </ul>
      </section>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[160px_1fr] gap-3 py-1 text-sm">
      <div className="text-slate-500">{k}</div>
      <div>{v}</div>
    </div>
  );
}
