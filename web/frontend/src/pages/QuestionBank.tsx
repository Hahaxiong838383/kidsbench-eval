import { useEffect, useRef, useState } from "react";

// ---- 类型（与 backend questionbank.py 对齐）----
type PipelineStep = { step: number; term: string; plain: string; detail: string };
type HarnessStep = { term: string; plain: string };
type Overview = {
  bank_version: string;
  raw_snapshots: string[];
  health: {
    total_in_jsonl: number;
    patched: number;
    with_current_session: number;
    negative_only: number;
    task_distribution: Record<string, number>;
  };
  fixes_summary: { total_patches: number; by_category: Record<string, number> };
  pipeline: PipelineStep[];
  harness: {
    term: string;
    plain: string;
    steps: HarnessStep[];
    baselines: { term: string; plain: string };
  };
};
type FixItem = {
  qid: string;
  category: string;
  problem: string;
  diagnosis: string;
  fix: string;
};
type BoardRow = {
  adapter: string;
  plain: string;
  avg_score: number;
  correct: number;
  wrong: number;
  evasive: number;
  error: number;
  avg_recall: number;
  write_tokens: number;
  read_tokens: number;
  token_note: string;
};
type Leaderboard = {
  groups: string[];
  board: BoardRow[];
  findings: { title: string; why: string }[];
};
type HistoryCol = {
  snapshot_id: string;
  label: string;
  created_at: string;
  n_questions: number;
};
type History = {
  total: number;
  snapshots: { snapshot_id: string; label: string; created_at: string; n_questions: number; notes: string }[];
  matrix: {
    adapters: string[];
    columns: HistoryCol[];
    cells: Record<string, (number | null)[]>;
    delta: Record<string, number | string>;
  };
};
type ParadigmCoverage = {
  home_ground: { adapter: string; paradigm: string; home: string[]; why: string }[];
  coverage: { task_type: string; name: string; count: number; minimum: number; status: string; starved_paradigms: string[] }[];
  suggestions: string[];
  principle: string;
};
type UploadReport = {
  uploaded_at: string;
  filename: string;
  healthy_questions: number;
  patched: number;
  reclassified: string[];
  skipped_non_memory: number;
  issues_total: number;
  issues_by_kind: Record<string, number>;
  issues: { qid: string; kind: string; detail: string }[];
  patch_failed_note: string;
  patch_failed: { qid: string; detail: string }[];
  next_step: string;
};

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} on ${path}`);
  return res.json() as Promise<T>;
}

export default function QuestionBank() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [fixes, setFixes] = useState<FixItem[] | null>(null);
  const [fixesNote, setFixesNote] = useState("");
  const [openFix, setOpenFix] = useState<string | null>(null);
  const [report, setReport] = useState<UploadReport | null>(null);
  const [lb, setLb] = useState<Leaderboard | null>(null);
  const [hist, setHist] = useState<History | null>(null);
  const [pc, setPc] = useState<ParadigmCoverage | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getJSON<Overview>("/api/questionbank").then(setOv);
    getJSON<Leaderboard>("/api/questionbank/leaderboard").then(setLb);
    getJSON<History>("/api/questionbank/leaderboard/history").then(setHist);
    getJSON<ParadigmCoverage>("/api/questionbank/paradigm-coverage").then(setPc);
    getJSON<{ note: string; items: FixItem[] }>("/api/questionbank/fixes").then(
      (d) => {
        setFixes(d.items);
        setFixesNote(d.note);
      },
    );
  }, []);

  async function onUpload(f: File) {
    setUploading(true);
    setUploadErr("");
    setReport(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch("/api/questionbank/upload", {
        method: "POST",
        body: fd,
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? `${res.status}`);
      setReport(body as UploadReport);
    } catch (e) {
      setUploadErr(String(e));
    } finally {
      setUploading(false);
    }
  }

  if (!ov) return <div className="text-slate-500">载入中…</div>;

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold">题库与转换管线</h1>
          <p className="text-sm text-slate-500 mt-1">
            当前题库：{ov.bank_version} ｜ 快照：{ov.raw_snapshots.join(", ") || "无"}
          </p>
        </div>
        <a
          href="/api/questionbank/export-analysis"
          className="px-4 py-2 rounded border border-emerald-600 text-emerald-700 text-sm hover:bg-emerald-50"
          download
        >
          导出分析报告（MD）
        </a>
      </div>

      {/* ===== 上传入口 ===== */}
      <section className="card space-y-3">
        <h2 className="text-lg font-semibold">上传新版题库 CSV</h2>
        <p className="text-sm text-slate-600">
          从飞书表导出 CSV 后上传。系统只做<b>转换 + 自动门禁检查</b>
          （秒级、不调任何大模型）：解析对话、套用修理补丁、锚定答案、跑红线扫描，
          然后给出健康度报告和修题清单。<b>不会覆盖正式题库</b>——确认无误后由维护者替换。
        </p>
        <div className="flex items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
              e.target.value = "";
            }}
          />
          <button
            className="px-4 py-2 rounded bg-emerald-600 text-white text-sm hover:bg-emerald-700 disabled:opacity-50"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? "转换中…" : "选择 CSV 上传"}
          </button>
          {uploadErr && <span className="text-sm text-red-600">{uploadErr}</span>}
        </div>

        {report && (
          <div className="border border-slate-200 rounded p-4 space-y-2 bg-slate-50">
            <div className="font-medium">
              转换报告 · {report.filename} · {report.uploaded_at}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
              <div className="bg-white rounded p-2">
                ✅ 健康可跑 <b>{report.healthy_questions}</b> 题
              </div>
              <div className="bg-white rounded p-2">
                🩹 套用补丁 <b>{report.patched}</b> 题
              </div>
              <div className="bg-white rounded p-2">
                ⏭️ 非记忆题跳过 <b>{report.skipped_non_memory}</b>
              </div>
              <div className="bg-white rounded p-2">
                ⚠️ 待修问题 <b>{report.issues_total}</b> 条
              </div>
            </div>
            {report.patch_failed.length > 0 && (
              <div className="text-sm text-amber-700 bg-amber-50 rounded p-2">
                <b>补丁失配 {report.patch_failed.length} 条</b>：{report.patch_failed_note}
                <ul className="mt-1 list-disc pl-5">
                  {report.patch_failed.map((p) => (
                    <li key={p.qid}>
                      {p.qid}: {p.detail}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {report.issues.length > 0 && (
              <details className="text-sm">
                <summary className="cursor-pointer text-slate-700">
                  修题清单（{report.issues_total} 条，按问题类型：
                  {Object.entries(report.issues_by_kind)
                    .map(([k, v]) => `${k}×${v}`)
                    .join("、")}）
                </summary>
                <table className="mt-2 w-full text-xs">
                  <tbody>
                    {report.issues.map((i, idx) => (
                      <tr key={idx} className="border-t border-slate-200">
                        <td className="py-1 pr-2 whitespace-nowrap font-mono">{i.qid}</td>
                        <td className="py-1 pr-2 whitespace-nowrap text-slate-500">{i.kind}</td>
                        <td className="py-1">{i.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
            <div className="text-xs text-slate-500">{report.next_step}</div>
          </div>
        )}
      </section>

      {/* ===== 评测总榜 ===== */}
      {lb && lb.board.length > 0 && (
        <section className="card space-y-3">
          <h2 className="text-lg font-semibold">评测总榜（最近一批全量）</h2>
          <p className="text-sm text-slate-600">
            按<b>平均分</b>排序（部分得分制：一题多条要点，命中几条得几分）。
            聚合自 {lb.groups.join("、")}。前三行是参照物不是被测系统：
            无记忆=地板、全文=贵但能跑的上限、Oracle=只喂标准答案那句。
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-500 border-b border-slate-200">
                  <th className="py-1.5 pr-3">系统</th>
                  <th className="py-1.5 pr-3">平均分</th>
                  <th className="py-1.5 pr-3">全对</th>
                  <th className="py-1.5 pr-3">答错</th>
                  <th className="py-1.5 pr-3">回避</th>
                  <th className="py-1.5 pr-3">召回率</th>
                  <th className="py-1.5 pr-3">内部开销</th>
                  <th className="py-1.5">一句话定位</th>
                </tr>
              </thead>
              <tbody>
                {lb.board.map((b) => (
                  <tr key={b.adapter} className="border-b border-slate-100 align-top">
                    <td className="py-1.5 pr-3 font-mono whitespace-nowrap">{b.adapter}</td>
                    <td className="py-1.5 pr-3 font-semibold">{b.avg_score.toFixed(3)}</td>
                    <td className="py-1.5 pr-3">{b.correct}</td>
                    <td className="py-1.5 pr-3">{b.wrong}</td>
                    <td className="py-1.5 pr-3">{b.evasive}</td>
                    <td className="py-1.5 pr-3">{b.avg_recall.toFixed(2)}</td>
                    <td className="py-1.5 pr-3 text-xs whitespace-nowrap">
                      {b.token_note ||
                        (b.write_tokens + b.read_tokens > 0
                          ? `写${(b.write_tokens / 10000).toFixed(0)}万/读${(b.read_tokens / 10000).toFixed(0)}万`
                          : "—")}
                    </td>
                    <td className="py-1.5 text-xs text-slate-600">{b.plain}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 className="text-base font-semibold pt-1">这轮跑出来的发现（自动生成，每条带依据）</h3>
          <div className="space-y-2">
            {lb.findings.map((f) => (
              <div key={f.title} className="bg-slate-50 rounded p-3">
                <div className="text-sm font-medium">{f.title}</div>
                <div className="text-xs text-slate-600 mt-1">{f.why}</div>
              </div>
            ))}
          </div>

          {hist && hist.total > 0 && (
            <>
              <h3 className="text-base font-semibold pt-2">历史对比（每次评测的快照）</h3>
              <p className="text-xs text-slate-500">
                每跑完一轮评测归档一份快照。列头括号里是题数——
                <b>题数不同的快照不可直接比分</b>（12 题调试轮 vs 149 题全量是不同口径）。
                Δ 列是最近两次同口径快照的升降。
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-slate-500 border-b border-slate-200">
                      <th className="py-1.5 pr-3">系统</th>
                      {hist.matrix.columns.map((c) => (
                        <th key={c.snapshot_id} className="py-1.5 pr-3 whitespace-nowrap" title={c.label}>
                          {c.created_at.slice(5, 16)}
                          <span className="text-slate-400">（{c.n_questions}题）</span>
                        </th>
                      ))}
                      {Object.keys(hist.matrix.delta).length > 0 && (
                        <th className="py-1.5">Δ 最近</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {hist.matrix.adapters.map((a) => {
                      const d = hist.matrix.delta[a];
                      return (
                        <tr key={a} className="border-b border-slate-100">
                          <td className="py-1.5 pr-3 font-mono whitespace-nowrap">{a}</td>
                          {hist.matrix.cells[a].map((v, i) => (
                            <td key={i} className="py-1.5 pr-3">
                              {v === null ? <span className="text-slate-300">—</span> : v.toFixed(3)}
                            </td>
                          ))}
                          {Object.keys(hist.matrix.delta).length > 0 && (
                            <td className="py-1.5 text-xs">
                              {typeof d === "number" ? (
                                <span className={d > 0 ? "text-emerald-600" : d < 0 ? "text-red-500" : "text-slate-400"}>
                                  {d > 0 ? "↑" : d < 0 ? "↓" : "="}{Math.abs(d).toFixed(3)}
                                </span>
                              ) : (
                                <span className="text-slate-300">—</span>
                              )}
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <details className="text-xs text-slate-500">
                <summary className="cursor-pointer">各快照说明（点开看每次评测的环境备注）</summary>
                <ul className="mt-1 space-y-1 list-disc pl-5">
                  {hist.snapshots.map((s) => (
                    <li key={s.snapshot_id}>
                      <b>{s.created_at}</b> · {s.label} · {s.n_questions} 题
                      {s.notes && <span className="text-slate-400">　{s.notes}</span>}
                    </li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </section>
      )}

      {/* ===== 健康度 ===== */}
      <section className="card space-y-2">
        <h2 className="text-lg font-semibold">当前题库健康度</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <div className="bg-slate-50 rounded p-3">
            机器可跑 <div className="text-2xl font-semibold">{ov.health.total_in_jsonl}</div>
          </div>
          <div className="bg-slate-50 rounded p-3">
            经补丁修复 <div className="text-2xl font-semibold">{ov.health.patched}</div>
          </div>
          <div className="bg-slate-50 rounded p-3">
            带当场对话 <div className="text-2xl font-semibold">{ov.health.with_current_session}</div>
          </div>
          <div className="bg-slate-50 rounded p-3">
            该遗忘型 <div className="text-2xl font-semibold">{ov.health.negative_only}</div>
          </div>
        </div>
        <div className="text-xs text-slate-500">
          题型分布：
          {Object.entries(ov.health.task_distribution)
            .map(([k, v]) => `${k}×${v}`)
            .join(" ｜ ")}
        </div>
      </section>

      {/* ===== 管线显性化 ===== */}
      <section className="card space-y-3">
        <h2 className="text-lg font-semibold">转换管线：CSV 怎么变成可评测的题</h2>
        <p className="text-sm text-slate-600">
          上传的 CSV 会依次经过下面 7 步。每步左边是术语，右边是白话。
        </p>
        <ol className="space-y-3">
          {ov.pipeline.map((s) => (
            <li key={s.step} className="flex gap-3">
              <div className="flex-none w-7 h-7 rounded-full bg-emerald-100 text-emerald-700 flex items-center justify-center text-sm font-semibold">
                {s.step}
              </div>
              <div>
                <div className="font-medium">{s.term}</div>
                <div className="text-sm text-slate-700">{s.plain}</div>
                <div className="text-xs text-slate-500 mt-0.5">{s.detail}</div>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* ===== Harness 显性化 ===== */}
      <section className="card space-y-3">
        <h2 className="text-lg font-semibold">{ov.harness.term}：题转好之后谁来考试</h2>
        <p className="text-sm text-slate-700">{ov.harness.plain}</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {ov.harness.steps.map((s) => (
            <div key={s.term} className="bg-slate-50 rounded p-3">
              <div className="text-sm font-medium">{s.term}</div>
              <div className="text-xs text-slate-600 mt-1">{s.plain}</div>
            </div>
          ))}
        </div>
        <div className="bg-amber-50 rounded p-3">
          <div className="text-sm font-medium">{ov.harness.baselines.term}</div>
          <div className="text-xs text-slate-700 mt-1">{ov.harness.baselines.plain}</div>
        </div>
      </section>

      {/* ===== 范式×题型覆盖（出题优化地图）===== */}
      {pc && (
        <section className="card space-y-3">
          <h2 className="text-lg font-semibold">范式 × 题型覆盖（出题优化地图）</h2>
          <p className="text-sm text-slate-700">{pc.principle}</p>

          <h3 className="text-base font-semibold">各题型当前覆盖</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-500 border-b border-slate-200">
                  <th className="py-1.5 pr-3">题型</th>
                  <th className="py-1.5 pr-3">现有</th>
                  <th className="py-1.5 pr-3">建议</th>
                  <th className="py-1.5 pr-3">状态</th>
                  <th className="py-1.5">主场受影响的范式</th>
                </tr>
              </thead>
              <tbody>
                {pc.coverage.map((c) => (
                  <tr key={c.task_type} className="border-b border-slate-100">
                    <td className="py-1.5 pr-3">{c.name}</td>
                    <td className="py-1.5 pr-3 font-semibold">{c.count}</td>
                    <td className="py-1.5 pr-3 text-slate-500">≥{c.minimum}</td>
                    <td className="py-1.5 pr-3">
                      <span
                        className={
                          c.status === "充足"
                            ? "text-emerald-600"
                            : c.status === "不足"
                              ? "text-amber-600"
                              : "text-red-600 font-semibold"
                        }
                      >
                        {c.status}
                      </span>
                    </td>
                    <td className="py-1.5 text-xs text-slate-600">
                      {c.starved_paradigms.join("、") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {pc.suggestions.length > 0 && (
            <div className="bg-amber-50 rounded p-3 space-y-1">
              <div className="text-sm font-medium">给出题侧的补题优先级</div>
              {pc.suggestions.map((s, i) => (
                <div key={i} className="text-xs text-slate-700">
                  {i + 1}. {s}
                </div>
              ))}
            </div>
          )}

          <details className="text-sm">
            <summary className="cursor-pointer font-medium">
              每个范式的主场是什么（点开看依据）
            </summary>
            <div className="mt-2 space-y-2">
              {pc.home_ground.map((h) => (
                <div key={h.adapter} className="bg-slate-50 rounded p-3">
                  <div className="text-sm">
                    <span className="font-mono font-medium">{h.adapter}</span>
                    <span className="text-slate-500">（{h.paradigm}）</span>
                    <span className="text-xs ml-2">主场：{h.home.join("、")}</span>
                  </div>
                  <div className="text-xs text-slate-600 mt-1">{h.why}</div>
                </div>
              ))}
            </div>
          </details>
        </section>
      )}

      {/* ===== 修理记录 ===== */}
      <section className="card space-y-3">
        <h2 className="text-lg font-semibold">
          修理记录（{ov.fixes_summary.total_patches} 个补丁）
        </h2>
        <p className="text-sm text-slate-600">{fixesNote}</p>
        <div className="text-xs text-slate-500">
          分类：
          {Object.entries(ov.fixes_summary.by_category)
            .map(([k, v]) => `${k}×${v}`)
            .join(" ｜ ")}
        </div>
        {fixes && (
          <div className="divide-y divide-slate-100">
            {fixes.map((f) => (
              <div key={f.qid + f.category} className="py-2">
                <button
                  className="w-full text-left flex items-center gap-3 text-sm"
                  onClick={() => setOpenFix(openFix === f.qid ? null : f.qid)}
                >
                  <span className="font-mono text-emerald-700 whitespace-nowrap">{f.qid}</span>
                  <span className="text-xs bg-slate-100 rounded px-1.5 py-0.5 whitespace-nowrap">
                    {f.category}
                  </span>
                  <span className="text-slate-600 truncate">{f.problem}</span>
                </button>
                {openFix === f.qid && (
                  <div className="mt-2 ml-2 space-y-1 text-sm border-l-2 border-emerald-200 pl-3">
                    <div>
                      <span className="text-slate-500">问题：</span>
                      {f.problem}
                    </div>
                    <div>
                      <span className="text-slate-500">依据：</span>
                      {f.diagnosis}
                    </div>
                    <div>
                      <span className="text-slate-500">修法：</span>
                      {f.fix}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
