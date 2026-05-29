import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { AdapterMeta, ContractInfo, MethodInfo } from "../lib/types";

export default function AdapterDetail() {
  const { name } = useParams<{ name: string }>();
  const [data, setData] = useState<AdapterMeta | null>(null);
  const [contract, setContract] = useState<ContractInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    setData(null);
    setErr(null);
    Promise.all([api.adapter(name), api.contract()])
      .then(([a, c]) => {
        setData(a);
        setContract(c);
      })
      .catch((e) => setErr(String(e)));
  }, [name]);

  if (err) return <div className="text-rose-600">载入失败：{err}</div>;
  if (!data || !contract) return <div className="text-slate-500">载入中…</div>;

  const abstractMethods = data.methods.filter((m) => m.kind === "abstract");
  const overridableMethods = data.methods.filter((m) => m.kind === "overridable");

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <header>
        <h1 className="text-2xl font-semibold">{data.name} Adapter</h1>
        <div className="text-xs text-slate-500 mt-1">
          {data.sdk.package} · v{data.sdk.version} ·{" "}
          <a
            className="text-emerald-600 hover:underline"
            href={data.sdk.github}
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
          {data.venv && (
            <>
              {" · "}
              <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">{data.venv}</code>
            </>
          )}
        </div>
      </header>

      {/* 基本信息 */}
      <Section title="基本信息">
        <KV k="SDK 包" v={<code className="font-mono">{data.sdk.package}</code>} />
        <KV k="版本" v={data.sdk.version} />
        <KV k="安装命令" v={<code className="font-mono text-xs bg-slate-100 px-2 py-0.5 rounded">{data.sdk.install}</code>} />
        <KV k="存储后端" v={data.storage} />
      </Section>

      {/* 入口类 */}
      <Section title="入口类">
        <KV
          k="Class"
          v={<code className="font-mono text-emerald-700 font-semibold">{data.entry_class.name}</code>}
        />
        <KV k="位置" v={<FileLineRef file={data.entry_class.file} line={data.entry_class.line} />} />
      </Section>

      {/* MemoryAdapter ABC 契约 */}
      <Section title="契约 · MemoryAdapter ABC">
        <p className="text-sm text-slate-700 mb-3">{contract.abc_class.doc}</p>
        <KV
          k="基类位置"
          v={<FileLineRef file={contract.abc_class.file} line={contract.abc_class.line} />}
        />
        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="label mb-2">7 个抽象方法（子类必须实现）</div>
            <div className="flex flex-wrap gap-1.5">
              {contract.abstract_methods.map((m) => (
                <span key={m} className="pill pill-blue font-mono">{m}</span>
              ))}
            </div>
          </div>
          <div>
            <div className="label mb-2">2 个可覆写方法（默认实现）</div>
            <div className="flex flex-wrap gap-1.5">
              {contract.overridable_methods.map((m) => (
                <span key={m} className="pill pill-violet font-mono">{m}</span>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-4">
          <div className="label mb-2">设计原则</div>
          <ul className="text-sm space-y-1">
            {contract.design_principles.map((p, i) => (
              <li key={i} className="text-slate-700">· {p}</li>
            ))}
          </ul>
        </div>
      </Section>

      {/* 7 个抽象方法实现 */}
      <Section title={`抽象方法实现（${abstractMethods.length} / 7）`}>
        <div className="space-y-4">
          {abstractMethods.map((m) => (
            <MethodCard key={m.name} method={m} />
          ))}
        </div>
      </Section>

      {/* 2 个可覆写方法（如有覆写） */}
      <Section title={`可覆写方法（${data.name} 覆写了 ${overridableMethods.length} / 2）`}>
        {overridableMethods.length === 0 ? (
          <div className="text-sm text-slate-500">本 adapter 均使用默认实现</div>
        ) : (
          <div className="space-y-4">
            {overridableMethods.map((m) => (
              <MethodCard key={m.name} method={m} variant="overridable" />
            ))}
          </div>
        )}
      </Section>

      {/* 中间件依赖 */}
      <Section title="中间件依赖">
        <ul className="space-y-1.5">
          {data.middleware_deps.map((d) => (
            <li key={d} className="text-sm text-slate-700 leading-relaxed">· {d}</li>
          ))}
        </ul>
      </Section>

      {/* 已知问题 */}
      {data.known_issues.length > 0 && (
        <Section title="已知问题">
          <ul className="space-y-1.5">
            {data.known_issues.map((issue, i) => (
              <li key={i} className="text-sm text-amber-700 leading-relaxed">
                ⚠ {issue}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function MethodCard({ method, variant = "abstract" }: { method: MethodInfo; variant?: "abstract" | "overridable" }) {
  const borderColor = variant === "abstract" ? "border-emerald-300" : "border-violet-300";
  const nameColor = variant === "abstract" ? "text-emerald-700" : "text-violet-700";
  const pillCls = variant === "abstract" ? "pill pill-blue" : "pill pill-violet";
  return (
    <div className={`border-l-2 ${borderColor} pl-4`}>
      <div className="flex items-baseline gap-2">
        <div className={`font-mono font-semibold ${nameColor}`}>{method.name}()</div>
        <span className={pillCls}>{method.kind}</span>
      </div>
      <div className="text-xs mt-1">
        <FileLineRef file={method.file} line={method.line} />
      </div>
      <div className="text-sm text-slate-700 mt-2 leading-relaxed">{method.logic}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card">
      <h2 className="text-lg font-semibold mb-3">{title}</h2>
      {children}
    </section>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 py-1.5 text-sm">
      <div className="text-slate-500">{k}</div>
      <div>{v}</div>
    </div>
  );
}

function FileLineRef({ file, line }: { file: string; line: number }) {
  return (
    <code className="font-mono text-xs text-emerald-700 hover:text-emerald-800">
      {file}:{line}
    </code>
  );
}
