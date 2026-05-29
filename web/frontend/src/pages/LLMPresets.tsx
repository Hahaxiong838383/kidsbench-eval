/**
 * LLM Preset 配置页（B1 Phase 3 Web UI）
 *
 * 功能：
 * - 列出所有 preset（脱敏显示）
 * - 新增自定义 LLM 表单（永不接收 raw api_key，只接收 env_var 名）
 * - 删除 preset
 *
 * 安全：
 * - api_key 永远在用户 .env.local（chmod 600，本地）
 * - web 端只管 preset 元信息（base_url / model / env_var 名）
 * - 添加后页面提示在 Air 上 .env.local 设置 KEY=value
 */
import { useEffect, useState } from "react";

interface PresetEmbedding {
  provider: string;
  model: string;
  dim: number;
}

interface PresetItem {
  name: string;
  display_name: string;
  provider: string;
  base_url: string;
  api_key_env: string;
  api_key_masked: string;
  model: string;
  max_tokens: number;
  reasoning_effort: string | null;
  configured: boolean;
  embedding: PresetEmbedding;
  error?: string;
}

interface CreateForm {
  name: string;
  display_name: string;
  provider: string;
  base_url: string;
  api_key_env: string;
  model: string;
  max_tokens: number;
  reasoning_effort: string;
  emb_provider: string;
  emb_model: string;
  emb_dim: number;
}

const EMPTY_FORM: CreateForm = {
  name: "",
  display_name: "",
  provider: "custom",
  base_url: "",
  api_key_env: "",
  model: "",
  max_tokens: 4096,
  reasoning_effort: "",
  emb_provider: "huggingface",
  emb_model: "BAAI/bge-small-zh-v1.5",
  emb_dim: 512,
};

export default function LLMPresets() {
  const [items, setItems] = useState<PresetItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch("/api/llm/presets")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => setItems(d.items ?? []))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [reloadKey]);

  const update = (k: keyof CreateForm, v: string | number) => {
    setForm((cur) => ({ ...cur, [k]: v }));
  };

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        name: form.name,
        display_name: form.display_name || form.name,
        provider: form.provider,
        base_url: form.base_url,
        api_key_env: form.api_key_env,
        model: form.model,
        max_tokens: form.max_tokens,
        reasoning_effort: form.reasoning_effort || null,
        embedding: {
          provider: form.emb_provider,
          model: form.emb_model,
          dim: form.emb_dim,
        },
      };
      const r = await fetch("/api/llm/presets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status} · ${txt}`);
      }
      setShowForm(false);
      setForm(EMPTY_FORM);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (name: string) => {
    if (!confirm(`删除 preset "${name}"？此操作不可撤销`)) return;
    const r = await fetch(`/api/llm/presets/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    if (r.ok) {
      setReloadKey((k) => k + 1);
    } else {
      alert(`删除失败：HTTP ${r.status}`);
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">LLM 配置</h1>
        <p className="text-sm text-slate-500 mt-1">
          管理可用的 LLM Preset。Preset 文件存元信息（base_url / model / env_var 名），
          真实 API key 永远在 Air 上的 <code className="font-mono">.env.local</code>
          （chmod 600 / .gitignored）。
        </p>
      </header>

      {/* preset 列表 */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">已配置 Preset（{items.length}）</h2>
          <button
            type="button"
            onClick={() => setShowForm((s) => !s)}
            className="text-sm bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded"
          >
            {showForm ? "取消" : "+ 添加自定义 LLM"}
          </button>
        </div>

        {error && (
          <div className="card mb-3 bg-rose-50 border-rose-200 text-rose-800 text-sm">
            ⚠ {error}
          </div>
        )}

        {loading ? (
          <div className="text-slate-500 text-sm">载入中…</div>
        ) : items.length === 0 ? (
          <div className="card text-slate-500 text-sm">还没有 preset</div>
        ) : (
          <div className="space-y-3">
            {items.map((p) => (
              <PresetCard key={p.name} preset={p} onDelete={() => remove(p.name)} />
            ))}
          </div>
        )}
      </section>

      {/* 新增表单 */}
      {showForm && (
        <section className="card border-l-4 border-l-emerald-500">
          <h2 className="text-lg font-semibold mb-3">添加自定义 LLM</h2>
          <p className="text-xs text-slate-500 mb-4">
            提交后请在 Air 上的{" "}
            <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded">.env.local</code>{" "}
            里设置 <code className="font-mono">{form.api_key_env || "<API_KEY_ENV>"}</code> 才能跑题
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Field label="Preset 名*" hint="只允许 [a-z0-9.-_] 不以.开头">
              <input
                value={form.name}
                onChange={(e) => update("name", e.target.value)}
                placeholder="如：deepseek-chat / gpt-4o"
                className="input"
              />
            </Field>
            <Field label="显示名">
              <input
                value={form.display_name}
                onChange={(e) => update("display_name", e.target.value)}
                placeholder="如：DeepSeek Chat（默认用 name）"
                className="input"
              />
            </Field>
            <Field label="Provider 标签">
              <input
                value={form.provider}
                onChange={(e) => update("provider", e.target.value)}
                placeholder="如：deepseek / openai / kimi / openrouter"
                className="input"
              />
            </Field>
            <Field label="Model*">
              <input
                value={form.model}
                onChange={(e) => update("model", e.target.value)}
                placeholder="如：deepseek-chat / gpt-4o-mini"
                className="input"
              />
            </Field>
            <Field label="Base URL*" hint="OpenAI 兼容 endpoint（含 /v1）">
              <input
                value={form.base_url}
                onChange={(e) => update("base_url", e.target.value)}
                placeholder="https://api.deepseek.com/v1"
                className="input"
              />
            </Field>
            <Field
              label="API Key Env 名*"
              hint="只填环境变量名，不填值"
            >
              <input
                value={form.api_key_env}
                onChange={(e) => update("api_key_env", e.target.value.toUpperCase())}
                placeholder="KIDSBENCH_DEEPSEEK_API_KEY"
                className="input"
              />
            </Field>
            <Field label="Max Tokens">
              <input
                type="number"
                value={form.max_tokens}
                onChange={(e) => update("max_tokens", Number(e.target.value))}
                className="input"
              />
            </Field>
            <Field label="Reasoning Effort（可选）" hint="如 minimal/low/medium/high">
              <input
                value={form.reasoning_effort}
                onChange={(e) => update("reasoning_effort", e.target.value)}
                placeholder="DeepSeek 留空"
                className="input"
              />
            </Field>
          </div>

          <h3 className="text-sm font-semibold mt-5 mb-2 text-slate-700">Embedding</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
            <Field label="Provider">
              <input
                value={form.emb_provider}
                onChange={(e) => update("emb_provider", e.target.value)}
                placeholder="huggingface"
                className="input"
              />
            </Field>
            <Field label="Model">
              <input
                value={form.emb_model}
                onChange={(e) => update("emb_model", e.target.value)}
                placeholder="BAAI/bge-small-zh-v1.5"
                className="input"
              />
            </Field>
            <Field label="Dim">
              <input
                type="number"
                value={form.emb_dim}
                onChange={(e) => update("emb_dim", Number(e.target.value))}
                className="input"
              />
            </Field>
          </div>

          <div className="mt-5 flex gap-3">
            <button
              type="button"
              onClick={submit}
              disabled={
                submitting || !form.name || !form.base_url || !form.model || !form.api_key_env
              }
              className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "保存中…" : "保存 Preset"}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowForm(false);
                setForm(EMPTY_FORM);
              }}
              className="bg-slate-100 hover:bg-slate-200 text-slate-700 px-4 py-2 rounded"
            >
              取消
            </button>
          </div>
        </section>
      )}
    </div>
  );
}

function PresetCard({ preset, onDelete }: { preset: PresetItem; onDelete: () => void }) {
  if (preset.error) {
    return (
      <div className="card border-rose-300">
        <div className="flex items-center justify-between">
          <div>
            <div className="font-mono font-semibold">{preset.name}</div>
            <div className="text-xs text-rose-600 mt-1">⚠ 解析错误：{preset.error}</div>
          </div>
          <button type="button" onClick={onDelete} className="text-xs text-rose-600 hover:underline">
            删除
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-baseline gap-2">
            <div className="text-base font-semibold">{preset.display_name}</div>
            <span className="pill pill-zinc text-[10px]">{preset.provider}</span>
            {preset.configured ? (
              <span className="pill pill-green text-[10px]">已配置</span>
            ) : (
              <span className="pill pill-amber text-[10px]">⚠ KEY 未设</span>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1 mt-3 text-sm">
            <KV k="name" v={<code className="font-mono">{preset.name}</code>} />
            <KV k="model" v={<code className="font-mono text-emerald-700">{preset.model}</code>} />
            <KV k="base_url" v={<code className="font-mono text-xs break-all">{preset.base_url}</code>} />
            <KV k="max_tokens" v={String(preset.max_tokens)} />
            <KV
              k="api_key_env"
              v={<code className="font-mono text-xs">{preset.api_key_env}</code>}
            />
            <KV
              k="key 状态"
              v={
                <code
                  className={`font-mono text-xs ${preset.configured ? "text-emerald-700" : "text-amber-700"}`}
                >
                  {preset.api_key_masked}
                </code>
              }
            />
            {preset.reasoning_effort && (
              <KV k="reasoning" v={preset.reasoning_effort} />
            )}
            <KV
              k="embedding"
              v={
                <span className="text-xs">
                  {preset.embedding.provider} · {preset.embedding.model} · {preset.embedding.dim}d
                </span>
              }
            />
          </div>
        </div>
        <button
          type="button"
          onClick={onDelete}
          className="text-xs text-rose-600 hover:underline ml-3 flex-shrink-0"
        >
          删除
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-600 mb-1">{label}</label>
      {children}
      {hint && <div className="text-[10px] text-slate-500 mt-0.5">{hint}</div>}
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-xs text-slate-500 w-20 flex-shrink-0">{k}</span>
      <span className="text-sm">{v}</span>
    </div>
  );
}
