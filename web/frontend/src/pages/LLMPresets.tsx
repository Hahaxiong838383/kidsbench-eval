/**
 * LLM Preset 配置页（B1 Phase 3 + UX 改进）
 *
 * 改进：
 * - 表单 model 输入加 datalist (常见模型下拉建议)
 * - 切换 model 时 max_tokens 自动联动 (默认值 + max 上限)
 * - 卡片加「编辑」按钮（PATCH /api/llm/presets/{name}）
 * - 「未配置」状态详细说明（解释 secret 在 Air 不在 container）
 */
import { useEffect, useMemo, useState } from "react";
import {
  getModelLimits,
  listModelNames,
  type ModelLimits,
} from "../lib/modelPresets";

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

interface FormState {
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

const EMPTY_FORM: FormState = {
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

type FormMode = "create" | { kind: "edit"; original: PresetItem };

export default function LLMPresets() {
  const [items, setItems] = useState<PresetItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<FormMode | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
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

  const limits = useMemo<ModelLimits>(() => getModelLimits(form.model), [form.model]);

  const update = (k: keyof FormState, v: string | number) => {
    setForm((cur) => {
      const next = { ...cur, [k]: v };
      // 切换 model 时自动联动 max_tokens
      if (k === "model" && typeof v === "string") {
        const lim = getModelLimits(v);
        // 如果用户当前 max_tokens 高于新 model 上限，下调；
        // 否则若是表单初始默认 4096，跟随新 model 的 default 值
        if (cur.max_tokens > lim.max) {
          next.max_tokens = lim.default;
        }
      }
      return next;
    });
  };

  const openCreate = () => {
    setMode("create");
    setForm(EMPTY_FORM);
    setError(null);
  };

  const openEdit = (p: PresetItem) => {
    setMode({ kind: "edit", original: p });
    setForm({
      name: p.name,
      display_name: p.display_name,
      provider: p.provider,
      base_url: p.base_url,
      api_key_env: p.api_key_env,
      model: p.model,
      max_tokens: p.max_tokens,
      reasoning_effort: p.reasoning_effort ?? "",
      emb_provider: p.embedding.provider,
      emb_model: p.embedding.model,
      emb_dim: p.embedding.dim,
    });
    setError(null);
  };

  const closeForm = () => {
    setMode(null);
    setForm(EMPTY_FORM);
    setError(null);
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
      const isEdit = mode !== null && mode !== "create";
      const url = isEdit
        ? `/api/llm/presets/${encodeURIComponent(form.name)}`
        : "/api/llm/presets";
      const method = isEdit ? "PATCH" : "POST";
      const r = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status} · ${txt}`);
      }
      closeForm();
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

  const isEditMode = mode !== null && mode !== "create";

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">LLM 配置</h1>
        <p className="text-sm text-slate-500 mt-1">
          管理可用的 LLM Preset。Preset 文件只存元信息（base_url / model / env_var 名），
          真实 API key 永远在你机器上的 <code className="font-mono">.env.local</code>
          （chmod 600 + .gitignored），不会上传到公网。
        </p>
      </header>

      {/* 「未配置」的全局说明 banner */}
      <div className="bg-amber-50 border border-amber-200 text-amber-800 text-sm rounded p-3">
        <strong>「key 状态：&lt;未配置&gt;」是什么意思？</strong>
        <div className="mt-1.5 text-amber-700">
          公网 backend（QNAP 容器）出于安全**不存任何 API key**，所以这里全部显示「未配置」是正常的。
          真实 key 在你 Air 上的{" "}
          <code className="font-mono bg-amber-100 px-1 py-0.5 rounded">~/mycc/kidsbench-eval/.env.local</code>{" "}
          —— 只在你本地跑评测时使用。
          这个页面的作用是<strong>管理 preset 元信息</strong>（base_url / model / env_var 名），
          跑题时由本地 harness 读 `.env.local` 注入真 key。
        </div>
      </div>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">已配置 Preset（{items.length}）</h2>
          {mode === null && (
            <button
              type="button"
              onClick={openCreate}
              className="text-sm bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded"
            >
              + 添加自定义 LLM
            </button>
          )}
        </div>

        {error && !mode && (
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
              <PresetCard
                key={p.name}
                preset={p}
                onEdit={() => openEdit(p)}
                onDelete={() => remove(p.name)}
              />
            ))}
          </div>
        )}
      </section>

      {/* 添加 / 编辑表单 */}
      {mode && (
        <section className="card border-l-4 border-l-emerald-500">
          <h2 className="text-lg font-semibold mb-3">
            {isEditMode ? `编辑 Preset：${form.name}` : "添加自定义 LLM"}
          </h2>
          {!isEditMode && (
            <p className="text-xs text-slate-500 mb-4">
              提交后请在 Air 上的{" "}
              <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded">
                .env.local
              </code>{" "}
              里设置{" "}
              <code className="font-mono">
                {form.api_key_env || "<API_KEY_ENV>"}
              </code>{" "}
              才能跑题
            </p>
          )}

          {error && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 text-sm rounded p-2 mb-3">
              ⚠ {error}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Field label="Preset 名*" hint="只允许 [a-z0-9.-_] 不以.开头">
              <input
                value={form.name}
                onChange={(e) => update("name", e.target.value)}
                placeholder="如：deepseek-chat / gpt-4o"
                disabled={isEditMode}
                className={`input ${isEditMode ? "opacity-60 cursor-not-allowed" : ""}`}
              />
            </Field>
            <Field label="显示名">
              <input
                value={form.display_name}
                onChange={(e) => update("display_name", e.target.value)}
                placeholder="DeepSeek Chat（默认用 name）"
                className="input"
              />
            </Field>
            <Field label="Provider 标签">
              <input
                value={form.provider}
                onChange={(e) => update("provider", e.target.value)}
                placeholder="如：deepseek / openai / kimi"
                className="input"
              />
            </Field>
            <Field
              label="Model*"
              hint="可输入任意 model 名，下方有常见选项自动补全"
            >
              <input
                value={form.model}
                onChange={(e) => update("model", e.target.value)}
                placeholder="如：deepseek-chat / gpt-4o-mini"
                list="model-suggestions"
                className="input font-mono"
              />
              <datalist id="model-suggestions">
                {listModelNames().map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </Field>
            <Field label="Base URL*" hint="OpenAI 兼容 endpoint（含 /v1）">
              <input
                value={form.base_url}
                onChange={(e) => update("base_url", e.target.value)}
                placeholder="https://api.deepseek.com/v1"
                className="input"
              />
            </Field>
            <Field label="API Key Env 名*" hint="只填环境变量名，不填值">
              <input
                value={form.api_key_env}
                onChange={(e) =>
                  update("api_key_env", e.target.value.toUpperCase())
                }
                placeholder="KIDSBENCH_DEEPSEEK_API_KEY"
                className="input font-mono"
              />
            </Field>
            <Field
              label={`Max Tokens（${form.max_tokens} / 上限 ${limits.max.toLocaleString()}）`}
              hint={
                limits.hint
                  ? `${limits.hint}（model 上限 ${limits.max.toLocaleString()}）`
                  : `当前 model 上限：${limits.max.toLocaleString()}`
              }
            >
              <div className="flex items-center gap-2">
                <input
                  type="range"
                  min={256}
                  max={limits.max}
                  step={256}
                  value={form.max_tokens}
                  onChange={(e) => update("max_tokens", Number(e.target.value))}
                  className="flex-1 accent-emerald-600"
                />
                <input
                  type="number"
                  min={256}
                  max={limits.max}
                  step={256}
                  value={form.max_tokens}
                  onChange={(e) => update("max_tokens", Number(e.target.value))}
                  className="input w-24"
                />
              </div>
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

          <h3 className="text-sm font-semibold mt-5 mb-2 text-slate-700">
            Embedding
          </h3>
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
                className="input font-mono"
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
                submitting ||
                !form.name ||
                !form.base_url ||
                !form.model ||
                !form.api_key_env
              }
              className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "保存中…" : isEditMode ? "保存修改" : "保存 Preset"}
            </button>
            <button
              type="button"
              onClick={closeForm}
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

function PresetCard({
  preset,
  onEdit,
  onDelete,
}: {
  preset: PresetItem;
  onEdit: () => void;
  onDelete: () => void;
}) {
  if (preset.error) {
    return (
      <div className="card border-rose-300">
        <div className="flex items-center justify-between">
          <div>
            <div className="font-mono font-semibold">{preset.name}</div>
            <div className="text-xs text-rose-600 mt-1">
              ⚠ 解析错误：{preset.error}
            </div>
          </div>
          <button
            type="button"
            onClick={onDelete}
            className="text-xs text-rose-600 hover:underline"
          >
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
              <span
                className="pill pill-amber text-[10px]"
                title="container 不存 secret，真 key 在 Air .env.local"
              >
                未配置（仅 Air 本地有 key）
              </span>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1 mt-3 text-sm">
            <KV k="name" v={<code className="font-mono">{preset.name}</code>} />
            <KV
              k="model"
              v={
                <code className="font-mono text-emerald-700">{preset.model}</code>
              }
            />
            <KV
              k="base_url"
              v={
                <code className="font-mono text-xs break-all">
                  {preset.base_url}
                </code>
              }
            />
            <KV k="max_tokens" v={preset.max_tokens.toLocaleString()} />
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
                  {preset.embedding.provider} · {preset.embedding.model} ·{" "}
                  {preset.embedding.dim}d
                </span>
              }
            />
          </div>
        </div>
        <div className="flex flex-col gap-2 ml-3 flex-shrink-0">
          <button
            type="button"
            onClick={onEdit}
            className="text-xs text-emerald-700 hover:underline"
          >
            编辑
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="text-xs text-rose-600 hover:underline"
          >
            删除
          </button>
        </div>
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
      <label className="block text-xs font-medium text-slate-600 mb-1">
        {label}
      </label>
      {children}
      {hint && (
        <div className="text-[10px] text-slate-500 mt-0.5">{hint}</div>
      )}
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
