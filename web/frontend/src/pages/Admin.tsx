/**
 * Admin page (/admin) — phone CRUD + usage + settings. sessionStorage token (page-close expiry).
 * 401 auto-returns to login. Follows contract §3.2.
 */
import { useEffect, useState } from "react";
import type { AdminPhone, AdminSettings, AdminUsagePoint } from "../lib/types";

const ADMIN_TOKEN_KEY = "assistant_admin_token";

async function adminFetch<T>(path: string, init?: RequestInit, token?: string): Promise<T> {
  const t = token || sessionStorage.getItem(ADMIN_TOKEN_KEY) || "";
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(t ? { Authorization: `Bearer ${t}` } : {}),
      ...(init?.headers || {}),
    },
  });
  if (res.status === 401) {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    throw new Error("401");
  }
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(j?.detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return {} as T;
  return res.json() as Promise<T>;
}

export default function Admin() {
  const [authed, setAuthed] = useState<boolean>(!!sessionStorage.getItem(ADMIN_TOKEN_KEY));
  const [loginPwd, setLoginPwd] = useState("");
  const [loginErr, setLoginErr] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);

  const [phones, setPhones] = useState<AdminPhone[]>([]);
  const [usage, setUsage] = useState<AdminUsagePoint[]>([]);
  const [settings, setSettings] = useState<AdminSettings>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const [newPhone, setNewPhone] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [editing, setEditing] = useState<Record<string, Partial<AdminPhone>>>({});

  async function doLogin() {
    setLoginErr(""); setLoginBusy(true);
    try {
      const res = await fetch("/api/admin/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: loginPwd }),
      });
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(j?.detail || `登录失败 ${res.status}`); }
      const data = await res.json();
      sessionStorage.setItem(ADMIN_TOKEN_KEY, data.token);
      setAuthed(true); setLoginPwd(""); await loadAll(data.token);
    } catch (e) { setLoginErr(String(e).replace(/^Error: /, "")); } finally { setLoginBusy(false); }
  }
  function logout() {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    setAuthed(false); setPhones([]); setUsage([]); setSettings({});
  }

  async function loadPhones(t?: string) {
    const list = await adminFetch<{ items: AdminPhone[] }>("/api/admin/phones", {}, t);
    setPhones(list.items || []);
  }
  async function loadUsage() { const u = await adminFetch<{ items?: AdminUsagePoint[] }>("/api/admin/usage?days=7"); setUsage(u.items || []); }
  async function loadSettings() { const s = await adminFetch<AdminSettings>("/api/admin/settings"); setSettings(s || {}); }

  async function loadAll(t?: string) {
    setLoading(true); setErr("");
    try { await Promise.all([loadPhones(t), loadUsage(), loadSettings()]); }
    catch (e: any) { if (String(e).includes("401")) setAuthed(false); else setErr(String(e)); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (authed) loadAll().catch(() => {}); }, [authed]);

  async function addPhone() {
    if (!/^1\d{10}$/.test(newPhone)) { setErr("手机号格式需 1 开头 11 位数字"); return; }
    setErr("");
    try {
      await adminFetch("/api/admin/phones", { method: "POST", body: JSON.stringify({ phone: newPhone, label: newLabel || "", enabled: 1, daily_quota_tokens: 200000, daily_upgrade_limit: 5 }) });
      setNewPhone(""); setNewLabel(""); await loadPhones();
    } catch (e) { setErr(String(e)); }
  }

  function startEdit(p: AdminPhone) { setEditing((e) => ({ ...e, [p.phone]: { ...p } })); }
  function updateEdit(phone: string, field: keyof AdminPhone, val: string | number | boolean) {
    setEditing((e) => ({ ...e, [phone]: { ...e[phone], [field]: val } }));
  }
  async function saveEdit(phone: string) {
    const patch = editing[phone]; if (!patch) return;
    try {
      await adminFetch(`/api/admin/phones/${encodeURIComponent(phone)}`, { method: "PATCH", body: JSON.stringify({ label: patch.label, enabled: patch.enabled, daily_quota_tokens: patch.daily_quota_tokens, daily_upgrade_limit: patch.daily_upgrade_limit }) });
      setEditing((e) => { const { [phone]: _, ...rest } = e; return rest; });
      await loadPhones();
    } catch (e) { setErr(String(e)); }
  }
  function cancelEdit(phone: string) { setEditing((e) => { const { [phone]: _, ...rest } = e; return rest; }); }
  async function delPhone(phone: string) {
    if (!confirm(`确认删除 ${phone} ？`)) return;
    try { await adminFetch(`/api/admin/phones/${encodeURIComponent(phone)}`, { method: "DELETE" }); await loadPhones(); } catch (e) { setErr(String(e)); }
  }
  async function patchSetting(key: string, value: string) {
    try { await adminFetch("/api/admin/settings", { method: "PATCH", body: JSON.stringify({ [key]: value }) }); setSettings((s) => ({ ...s, [key]: value })); }
    catch (e) { setErr(String(e)); }
  }

  if (!authed) {
    return (
      <div className="max-w-sm mx-auto mt-12">
        <div className="card">
          <h1 className="text-xl font-semibold mb-1">管理员登录</h1>
          <p className="text-sm text-slate-500 mb-4">密码由环境 ASSISTANT_ADMIN_PASSWORD_SHA256 校验。</p>
          <input type="password" placeholder="输入管理员密码" value={loginPwd} onChange={(e) => setLoginPwd(e.target.value)} className="input mb-2" onKeyDown={(e) => e.key === "Enter" && doLogin()} />
          <button onClick={doLogin} disabled={loginBusy || !loginPwd} className="w-full px-4 py-2 bg-emerald-600 text-white rounded disabled:opacity-50">{loginBusy ? "登录中…" : "登录"}</button>
          {loginErr && <div className="text-red-600 text-sm mt-2">{loginErr}</div>}
          <div className="text-[10px] text-slate-400 mt-3">Token 存 sessionStorage，关页失效。</div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">AI 助手 · 管理后台</h1>
          <p className="text-sm text-slate-500">手机号授权 / 配额 / 设置</p>
        </div>
        <button onClick={logout} className="text-sm px-3 py-1 rounded border">退出登录</button>
      </div>

      {err && <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 text-sm rounded">{err}</div>}

      {/* Phones CRUD */}
      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">授权手机号</h2>
          <span className="text-xs text-slate-500">{phones.length} 人</span>
        </div>

        <div className="flex flex-wrap gap-2 items-end">
          <div><div className="text-xs text-slate-500 mb-0.5">手机号</div><input value={newPhone} onChange={(e) => setNewPhone(e.target.value.replace(/\D/g, "").slice(0, 11))} placeholder="13800001234" className="input w-40" /></div>
          <div><div className="text-xs text-slate-500 mb-0.5">备注</div><input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder="可选" className="input w-32" /></div>
          <button onClick={addPhone} disabled={!/^1\d{10}$/.test(newPhone)} className="px-3 py-1.5 rounded bg-emerald-600 text-white text-sm disabled:opacity-50">添加</button>
          <div className="text-xs text-slate-400 ml-1">默认 200k tokens / 升级 5 次</div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-slate-500 text-xs border-b">
              <th className="py-1.5 pr-3">手机号</th><th className="py-1.5 pr-3">备注</th><th className="py-1.5 pr-3">启用</th>
              <th className="py-1.5 pr-3">日配额</th><th className="py-1.5 pr-3">升级限额</th><th className="py-1.5 pr-3">今日已用</th><th className="py-1.5">操作</th>
            </tr></thead>
            <tbody>
              {phones.length === 0 && <tr><td colSpan={7} className="py-3 text-slate-400 text-sm">{loading ? "加载中…" : "暂无授权手机号"}</td></tr>}
              {phones.map((p) => {
                const ed = editing[p.phone]; const isEd = !!ed;
                return (
                  <tr key={p.phone} className="border-b border-slate-100 align-top">
                    <td className="py-1.5 pr-3 font-mono">{p.phone}</td>
                    <td className="py-1.5 pr-3">{isEd ? <input className="input text-xs py-0.5" value={ed.label ?? ""} onChange={(e) => updateEdit(p.phone, "label", e.target.value)} /> : (p.label || <span className="text-slate-400">—</span>)}</td>
                    <td className="py-1.5 pr-3">{isEd ? <select className="bg-white border text-xs px-1 py-0.5 rounded" value={ed.enabled ? "1" : "0"} onChange={(e) => updateEdit(p.phone, "enabled", e.target.value === "1")}><option value="1">是</option><option value="0">否</option></select> : (p.enabled ? <span className="pill pill-green">启用</span> : <span className="pill pill-zinc">禁用</span>)}</td>
                    <td className="py-1.5 pr-3">{isEd ? <input type="number" className="input w-20 text-xs py-0.5" value={ed.daily_quota_tokens ?? 200000} onChange={(e) => updateEdit(p.phone, "daily_quota_tokens", Number(e.target.value))} /> : (p.daily_quota_tokens || 0).toLocaleString()}</td>
                    <td className="py-1.5 pr-3">{isEd ? <input type="number" className="input w-16 text-xs py-0.5" value={ed.daily_upgrade_limit ?? 5} onChange={(e) => updateEdit(p.phone, "daily_upgrade_limit", Number(e.target.value))} /> : (p.daily_upgrade_limit ?? 5)}</td>
                    <td className="py-1.5 pr-3 text-xs text-slate-600">{p.today_used_tokens ?? 0}</td>
                    <td className="py-1.5 text-xs space-x-2">{isEd ? (<><button className="text-emerald-700" onClick={() => saveEdit(p.phone)}>保存</button><button onClick={() => cancelEdit(p.phone)}>取消</button></>) : (<><button className="text-emerald-700" onClick={() => startEdit(p)}>编辑</button><button className="text-red-600" onClick={() => delPhone(p.phone)}>删除</button></>)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Usage */}
      <section className="card space-y-3">
        <h2 className="text-lg font-semibold">近 7 天用量（phone × day）</h2>
        {usage.length === 0 ? <div className="text-sm text-slate-500">暂无数据</div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-slate-500 text-xs border-b"><th className="py-1.5 pr-3">手机号</th><th className="py-1.5 pr-3">日期</th><th className="py-1.5 pr-3">tokens</th><th className="py-1.5 pr-3">请求</th><th className="py-1.5 pr-3">升级</th><th className="py-1.5">降级</th></tr></thead>
              <tbody>{usage.map((u, i) => (<tr key={i} className="border-b border-slate-100"><td className="py-1 pr-3 font-mono">{u.phone}</td><td className="py-1 pr-3">{u.day}</td><td className="py-1 pr-3">{u.tokens}</td><td className="py-1 pr-3">{u.requests}</td><td className="py-1 pr-3">{u.upgrades}</td><td className="py-1 pr-3">{u.degraded}</td></tr>))}</tbody>
            </table>
          </div>
        )}
      </section>

      {/* Settings */}
      <section className="card space-y-3">
        <h2 className="text-lg font-semibold">全局设置</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 text-sm">
          {Object.keys(settings).length === 0 && <div className="text-slate-400 text-xs">（返回空时可直接输入键失焦 PATCH）</div>}
          {Object.entries(settings).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2"><div className="w-52 text-slate-600 font-mono text-xs truncate">{k}</div>
              <input className="input flex-1 text-xs py-0.5" defaultValue={v} onBlur={(e) => { if (e.target.value !== v) patchSetting(k, e.target.value); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
            </div>
          ))}
        </div>
      </section>

      <div className="text-[10px] text-slate-400">改动立即影响后端配额与路由。</div>
    </div>
  );
}
