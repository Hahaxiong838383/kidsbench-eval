import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type {
  BankSummary,
  BankDetail,
  BankCli,
  BankIssue,
} from "../lib/types";

// 规范题型顺序（用于横条展示 0 缺席）
const KNOWN_TASK_TYPES: string[] = [
  "T1_recall",
  "T2_consistency",
  "T3_update",
  "T4_interference",
  "T5_longterm",
  "T6_safety",
  "T7_noise",
];

// CLI adapter 选项（主 + 基线）
const ADAPTER_OPTIONS = [
  "cognee",
  "memmachine",
  "memobase",
  "mem0",
  "memoryos",
  "graphiti",
  "letta",
  "hindsight",
  "reme",
];
const BASELINE_OPTIONS = ["nomemory", "fullhistory", "oracle"];

const MAX_FILE_MB = 5;
const MAX_BYTES = MAX_FILE_MB * 1024 * 1024;

function formatDate(s: string) {
  try {
    return new Date(s).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return s;
  }
}

export default function Banks() {
  const [banks, setBanks] = useState<BankSummary[]>([]);
  const [current, setCurrent] = useState<BankDetail | null>(null);
  const [cli, setCli] = useState<BankCli | null>(null);
  const [selectedAdapters, setSelectedAdapters] = useState<string[]>([]);

  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [cliLoading, setCliLoading] = useState(false);

  const [uploadErr, setUploadErr] = useState("");
  const [listErr, setListErr] = useState("");
  const [detailErr, setDetailErr] = useState("");
  const [cliErr, setCliErr] = useState("");
  const [copyMsg, setCopyMsg] = useState("");

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  // 初次加载列表 + 自动选中最新一个
  useEffect(() => {
    loadList(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadList(autoSelectFirst = false) {
    setListLoading(true);
    setListErr("");
    try {
      const res = await api.listBanks();
      const list = res.banks || [];
      setBanks(list);
      if (autoSelectFirst && list.length > 0 && !current) {
        await loadDetail(list[0].version);
      }
    } catch (e) {
      setListErr(String(e));
    } finally {
      setListLoading(false);
    }
  }

  async function loadDetail(version: string) {
    setDetailLoading(true);
    setDetailErr("");
    setCli(null);
    setCliErr("");
    setCopyMsg("");
    try {
      const d = await api.getBank(version);
      setCurrent(d);
    } catch (e) {
      setDetailErr(String(e));
      setCurrent(null);
    } finally {
      setDetailLoading(false);
    }
  }

  // 上传处理（含 5MB 前端拦截）
  async function handleFile(file: File | undefined | null) {
    if (!file) return;
    setUploadErr("");
    setCli(null);
    setCopyMsg("");

    if (file.size > MAX_BYTES) {
      setUploadErr(`文件过大（${(file.size / 1024 / 1024).toFixed(1)}MB），最大允许 ${MAX_FILE_MB}MB`);
      return;
    }
    // 简单类型提示
    if (!file.name.toLowerCase().endsWith(".csv") && !file.type.includes("csv") && !file.type.includes("text")) {
      // 允许继续，服务端会再校验
    }

    setUploadLoading(true);
    try {
      const detail = await api.uploadBank(file);
      setCurrent(detail);
      // 刷新列表并确保最新在第一
      await loadList(false);
      // 上传结果即为当前选中
    } catch (e) {
      setUploadErr(String(e));
    } finally {
      setUploadLoading(false);
      // 清 input 以便重复选同名文件
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  // 拖拽
  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dropRef.current?.classList.add("!border-emerald-400", "!bg-emerald-50");
  }
  function onDragLeave(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dropRef.current?.classList.remove("!border-emerald-400", "!bg-emerald-50");
  }
  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dropRef.current?.classList.remove("!border-emerald-400", "!bg-emerald-50");
    const f = e.dataTransfer.files?.[0];
    handleFile(f);
  }

  // 点击选择
  function triggerSelect() {
    fileInputRef.current?.click();
  }

  // 版本列表点击
  function onSelectVersion(v: string) {
    if (current?.version === v) return;
    loadDetail(v);
  }

  // Adapter 多选切换
  function toggleAdapter(name: string) {
    setSelectedAdapters((prev) =>
      prev.includes(name) ? prev.filter((a) => a !== name) : [...prev, name]
    );
    // 切换后清旧结果，强制用户重新生成
    setCli(null);
    setCliErr("");
    setCopyMsg("");
  }

  // 生成 CLI
  async function generateCli() {
    if (!current || selectedAdapters.length === 0) return;
    setCliLoading(true);
    setCliErr("");
    setCli(null);
    setCopyMsg("");
    try {
      const res = await api.getBankCli(current.version, selectedAdapters);
      setCli(res);
    } catch (e) {
      setCliErr(String(e));
    } finally {
      setCliLoading(false);
    }
  }

  // 一键复制命令
  async function copyCommand() {
    if (!cli?.command) return;
    try {
      await navigator.clipboard.writeText(cli.command);
      setCopyMsg("已复制");
      setTimeout(() => setCopyMsg(""), 1600);
    } catch {
      setCopyMsg("复制失败，请手动全选复制");
    }
  }

  // 下载（直接 a 标签，浏览器处理 Content-Disposition）
  function downloadLink(kind: "questions" | "issues" | "source") {
    if (!current) return "#";
    return `/api/banks/${encodeURIComponent(current.version)}/download/${kind}`;
  }

  // 题型分布横条渲染
  function renderTaskDist(dist: Record<string, number>) {
    // 合并规范顺序 + 实际出现（兼容未来扩展）
    const entries: Array<[string, number]> = [];
    const seen = new Set<string>();

    for (const t of KNOWN_TASK_TYPES) {
      const v = dist[t] ?? 0;
      entries.push([t, v]);
      seen.add(t);
    }
    // 把后端返回但不在已知列表里的也展示（排后面）
    Object.entries(dist)
      .sort((a, b) => b[1] - a[1])
      .forEach(([k, v]) => {
        if (!seen.has(k)) entries.push([k, v]);
      });

    const total = entries.reduce((s, [, v]) => s + v, 0) || 1;

    return (
      <div className="space-y-2">
        {entries.map(([k, v]) => {
          const pct = Math.max(4, Math.min(100, Math.round((v / total) * 100)));
          const isAbsent = v === 0;
          return (
            <div key={k} className="flex items-center gap-3 text-sm">
              <div className="w-28 font-mono shrink-0 text-slate-700">{k}</div>
              <div className="flex-1 h-3 bg-slate-100 rounded overflow-hidden">
                <div
                  className={`h-3 rounded ${isAbsent ? "bg-slate-300" : "bg-emerald-500"}`}
                  style={{ width: `${isAbsent ? 100 : pct}%` }}
                />
              </div>
              <div className="w-20 text-right font-medium tabular-nums">
                {v}
                {isAbsent && <span className="ml-1 text-amber-600 text-xs">缺席</span>}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // issues 表格（前 200 已在后端截断）
  function renderIssues(issues: BankIssue[]) {
    if (!issues || issues.length === 0) {
      return <div className="text-sm text-emerald-600">无问题，全部健康。</div>;
    }
    return (
      <div className="overflow-x-auto border border-slate-200 rounded">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-500 border-b border-slate-200 bg-slate-50">
              <th className="py-1.5 px-3 font-normal">qid</th>
              <th className="py-1.5 px-3 font-normal">kind</th>
              <th className="py-1.5 px-3 font-normal">detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {issues.map((it, idx) => (
              <tr key={idx} className="align-top">
                <td className="py-1.5 px-3 font-mono text-xs text-slate-700 whitespace-nowrap">{it.qid}</td>
                <td className="py-1.5 px-3 text-xs text-amber-700 whitespace-nowrap">{it.kind}</td>
                <td className="py-1.5 px-3 text-xs text-slate-600">{it.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* 页头 */}
      <div>
        <h1 className="text-2xl font-semibold">题库上传</h1>
        <p className="text-sm text-slate-600 mt-1">
          上传 CSV 后端即时校验转换，返回健康摘要 + 题型分布 + issues（前 200）。版本不可变，按创建时间倒序。
          上传仅用于预览与 CLI 桥接，不进入正式排行榜。
        </p>
      </div>

      {/* ===== 1. 上传区 ===== */}
      <section className="card space-y-4">
        <h2 className="text-lg font-semibold">上传新题库 CSV</h2>
        <div className="text-sm text-slate-600">
          支持拖拽或点击选择。限制 ≤ {MAX_FILE_MB}MB、UTF-8 CSV。上传后立即返回该版本的完整健康报告，可直接用于下载与生成 CLI 命令。
        </div>

        {/* Dropzone */}
        <div
          ref={dropRef}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={triggerSelect}
          className="border-2 border-dashed border-slate-300 hover:border-emerald-300 rounded-lg p-8 text-center cursor-pointer bg-white active:bg-emerald-50 transition"
        >
          <div className="text-slate-500">
            拖拽 CSV 文件到此处，或 <span className="text-emerald-700 font-medium">点击选择文件</span>
          </div>
          <div className="text-xs text-slate-400 mt-1">仅支持 .csv · 最大 {MAX_FILE_MB}MB</div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />

        <div className="flex items-center gap-3">
          <button
            onClick={triggerSelect}
            disabled={uploadLoading}
            className="px-4 py-2 rounded bg-emerald-600 text-white text-sm hover:bg-emerald-700 disabled:opacity-60"
          >
            {uploadLoading ? "上传转换中…" : "选择 CSV 文件"}
          </button>
          <button
            onClick={() => loadList(false)}
            className="px-3 py-2 rounded border border-slate-300 text-sm hover:bg-slate-50"
          >
            刷新版本列表
          </button>
          {uploadLoading && <span className="text-sm text-slate-500">正在处理，请稍候（通常数秒）…</span>}
        </div>

        {uploadErr && (
          <div className="text-sm bg-red-50 border border-red-200 text-red-700 rounded px-3 py-2">{uploadErr}</div>
        )}
      </section>

      {/* ===== 当前选中版本详情（上传或列表点击后） ===== */}
      {current && (
        <section className="card space-y-6">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <div className="text-xs text-slate-500">当前版本</div>
              <div className="font-semibold font-mono text-lg text-emerald-700">{current.version}</div>
              <div className="text-sm text-slate-600 mt-0.5">
                {current.original_filename} · {formatDate(current.created_at)}
              </div>
            </div>
            <div className="flex gap-2">
              <a
                href={downloadLink("questions")}
                download
                className="px-3 py-1.5 rounded border border-emerald-600 text-emerald-700 text-sm hover:bg-emerald-50"
              >
                下载 questions.jsonl
              </a>
              <a
                href={downloadLink("issues")}
                download
                className="px-3 py-1.5 rounded border border-emerald-600 text-emerald-700 text-sm hover:bg-emerald-50"
              >
                下载 issues.csv
              </a>
              <a
                href={downloadLink("source")}
                download
                className="px-3 py-1.5 rounded border border-slate-300 text-slate-700 text-sm hover:bg-slate-50"
              >
                下载 source.csv
              </a>
            </div>
          </div>

          {/* 健康摘要卡片 */}
          <div>
            <div className="text-sm font-medium mb-2 text-slate-700">健康摘要</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-slate-50 rounded p-3">
                <div className="text-xs text-slate-500">总行数</div>
                <div className="text-2xl font-semibold tabular-nums">{current.health.total_rows}</div>
              </div>
              <div className="bg-emerald-50 border border-emerald-200 rounded p-3">
                <div className="text-xs text-emerald-700">健康可跑</div>
                <div className="text-2xl font-semibold text-emerald-700 tabular-nums">
                  {current.health.healthy}
                </div>
              </div>
              <div className="bg-amber-50 border border-amber-200 rounded p-3">
                <div className="text-xs text-amber-700">问题 / 跳过</div>
                <div className="text-2xl font-semibold text-amber-700 tabular-nums">
                  {current.issues_count} <span className="text-base align-super">/ {current.health.skipped}</span>
                </div>
              </div>
              <div className="bg-slate-50 rounded p-3">
                <div className="text-xs text-slate-500">红线丢弃 / 其他</div>
                <div className="text-2xl font-semibold tabular-nums">
                  {current.health.dropped_redline} <span className="text-base align-super text-slate-400">/ {current.health.skipped - current.issues_count}</span>
                </div>
              </div>
            </div>
            <div className="mt-2 text-sm">
              题量：<b className="tabular-nums">{current.question_count}</b> 题
              （健康 {current.health.healthy} + 问题 {current.issues_count}）
            </div>
          </div>

          {/* 题型分布横条 */}
          <div>
            <div className="text-sm font-medium mb-2 text-slate-700">题型分布（0 标为缺席）</div>
            {renderTaskDist(current.task_type_dist)}
          </div>

          {/* issues 表格 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="text-sm font-medium text-slate-700">
                问题明细（前 {Math.min(200, current.issues?.length || 0)} 条）
              </div>
              {current.issues_count > 0 && (
                <a
                  href={downloadLink("issues")}
                  download
                  className="text-xs text-emerald-700 hover:underline"
                >
                  下载完整 issues.csv →
                </a>
              )}
            </div>
            {renderIssues(current.issues || [])}
          </div>

          {/* 状态提示 */}
          <div className="text-xs text-slate-500">
            该版本不可变，重复同内容 CSV 将返回已有版本。
          </div>
        </section>
      )}

      {detailLoading && <div className="text-sm text-slate-500">加载版本详情中…</div>}
      {detailErr && <div className="text-sm bg-red-50 border border-red-200 text-red-700 rounded px-3 py-2">{detailErr}</div>}

      {/* ===== 3. CLI 命令桥 ===== */}
      <section className="card space-y-4">
        <div>
          <h2 className="text-lg font-semibold">CLI 命令桥</h2>
          <p className="text-sm text-slate-600 mt-1">
            选中适配器后生成可直接复制的 bash 命令（含 curl 下载该版本 jsonl + 对应 venv 提示 + run_eval）。
            命令会自动注入各 adapter 所需 ENV。
          </p>
        </div>

        {!current && <div className="text-sm text-slate-500">请先上传或从下方版本列表选择一个版本。</div>}

        {current && (
          <>
            {/* 多选 */}
            <div>
              <div className="text-sm font-medium mb-1.5">选择要评测的系统（可多选）</div>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-4 gap-y-1 text-sm">
                {ADAPTER_OPTIONS.map((a) => (
                  <label key={a} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedAdapters.includes(a)}
                      onChange={() => toggleAdapter(a)}
                    />
                    <span className="font-mono">{a}</span>
                  </label>
                ))}
                <div className="col-span-full text-xs text-slate-500 pt-1">基线对照（推荐同时勾选做上下界）：</div>
                {BASELINE_OPTIONS.map((a) => (
                  <label key={a} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedAdapters.includes(a)}
                      onChange={() => toggleAdapter(a)}
                    />
                    <span className="font-mono">{a}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={generateCli}
                disabled={cliLoading || selectedAdapters.length === 0}
                className="px-4 py-2 rounded bg-emerald-600 text-white text-sm hover:bg-emerald-700 disabled:opacity-50"
              >
                {cliLoading ? "生成中…" : "生成 CLI 命令"}
              </button>
              {selectedAdapters.length === 0 && (
                <span className="text-xs text-amber-600">至少选择一个系统</span>
              )}
            </div>

            {cliErr && (
              <div className="text-sm bg-red-50 border border-red-200 text-red-700 rounded px-3 py-2">{cliErr}</div>
            )}

            {cli && (
              <div className="space-y-3 border border-slate-200 rounded p-4 bg-slate-50">
                <div className="flex items-center justify-between">
                  <div className="font-medium text-sm">可执行命令（已适配该版本）</div>
                  <button
                    onClick={copyCommand}
                    className="text-xs px-2 py-1 rounded border border-emerald-600 text-emerald-700 hover:bg-emerald-50"
                  >
                    {copyMsg || "复制到剪贴板"}
                  </button>
                </div>

                <pre className="bg-slate-950 text-emerald-100 p-3 rounded text-[12px] leading-relaxed overflow-x-auto font-mono whitespace-pre">
{cli.command}
                </pre>

                <div className="text-sm">
                  估算：{cli.est.minutes_rough}（{cli.est.adapters} 个系统，约 {cli.est.questions} 题）
                </div>

                {cli.est.warn && (
                  <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
                    ⚠️ {cli.est.warn}
                  </div>
                )}

                {cli.note && <div className="text-xs text-slate-500">{cli.note}</div>}
              </div>
            )}
          </>
        )}
      </section>

      {/* ===== 2 + 4. 版本列表（点选加载详情） + 下载提示 ===== */}
      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">历史版本列表（点击行加载详情）</h2>
          <button
            onClick={() => loadList(false)}
            disabled={listLoading}
            className="text-sm px-3 py-1 border rounded hover:bg-slate-50 disabled:opacity-60"
          >
            {listLoading ? "加载中…" : "刷新"}
          </button>
        </div>

        {listErr && (
          <div className="text-sm bg-red-50 border border-red-200 text-red-700 rounded px-3 py-2">{listErr}</div>
        )}

        {banks.length === 0 && !listLoading && (
          <div className="text-sm text-slate-500">暂无版本，请先上传 CSV。</div>
        )}

        {banks.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-500 border-b border-slate-200">
                  <th className="py-1.5 pr-3">version</th>
                  <th className="py-1.5 pr-3">创建时间</th>
                  <th className="py-1.5 pr-3">题数</th>
                  <th className="py-1.5 pr-3">问题数</th>
                  <th className="py-1.5 pr-3">原始文件</th>
                  <th className="py-1.5 pr-3">status</th>
                </tr>
              </thead>
              <tbody>
                {banks.map((b) => {
                  const isActive = current?.version === b.version;
                  return (
                    <tr
                      key={b.version}
                      onClick={() => onSelectVersion(b.version)}
                      className={`border-b border-slate-100 cursor-pointer hover:bg-emerald-50/40 ${isActive ? "bg-emerald-50" : ""}`}
                    >
                      <td className="py-1.5 pr-3 font-mono text-emerald-700">{b.version}</td>
                      <td className="py-1.5 pr-3 text-xs text-slate-600 whitespace-nowrap">{formatDate(b.created_at)}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{b.question_count}</td>
                      <td className="py-1.5 pr-3 tabular-nums text-amber-600">{b.issues_count}</td>
                      <td className="py-1.5 pr-3 text-xs text-slate-600 truncate max-w-[220px]">{b.original_filename}</td>
                      <td className="py-1.5 pr-3">
                        <span className="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{b.status}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="text-xs text-slate-500">
          点击任意行即可切换「当前版本」，用于查看健康详情、题型分布、issues 及生成对应 CLI 命令。
        </div>
      </section>
    </div>
  );
}
