/**
 * Group 详情页（B1.4）：/runs/:group
 *
 * 内容：
 * - group 概览（target_adapter / era / summary 成绩矩阵）
 * - results.jsonl 表格（每 experiment 一行）
 * - 每行点开看 PipelineTimeline（前提：harness 跑时带 --trace）
 *
 * 设计：
 * - 行内 accordion 展开（不跳页，节省切换）
 * - 没 pipeline 数据时友好提示
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type {
  AdapterStats,
  ExperimentRow,
  PipelineListItem,
  PipelineResponse,
  SpanEvent,
} from "../lib/types";
import PipelineTimeline from "../components/PipelineTimeline";

interface GroupDetail {
  name: string;
  target_adapter: string;
  era: string;
  summary: Record<string, AdapterStats> | null;
  results_count: number;
  results: ExperimentRow[];
}

export default function GroupDetail() {
  const { group } = useParams<{ group: string }>();
  const [data, setData] = useState<GroupDetail | null>(null);
  const [pipelines, setPipelines] = useState<PipelineListItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    if (!group) return;
    setData(null);
    setErr(null);
    api
      .runGroup(group)
      .then((d) => setData(d as GroupDetail))
      .catch((e) => setErr(String(e)));
    api
      .listPipelines(group)
      .then((r) => setPipelines(r.pipelines))
      .catch(() => setPipelines([]));
  }, [group]);

  if (err) return <div className="text-rose-600">载入失败：{err}</div>;
  if (!data) return <div className="text-slate-500">载入中…</div>;

  const pipelineMap = new Map<string, PipelineListItem>();
  for (const p of pipelines) {
    pipelineMap.set(`${p.adapter}__${p.qid}`, p);
  }

  return (
    <div className="space-y-5">
      <header>
        <div className="flex items-center gap-3">
          <Link to="/runs" className="text-sm text-emerald-700 hover:underline">
            ← 历史 Run Groups
          </Link>
        </div>
        <h1 className="text-2xl font-semibold mt-2">{data.name}</h1>
        <div className="text-xs text-slate-500 mt-1 flex gap-3">
          <span>目标 Adapter: <code className="font-mono">{data.target_adapter}</code></span>
          <span>时代: <code className="font-mono">{data.era}</code></span>
          <span>{data.results_count} 个 experiment</span>
          <span>{pipelines.length} 个 pipeline trace</span>
        </div>
      </header>

      {/* summary 成绩矩阵 */}
      {data.summary && (
        <section className="card">
          <h2 className="text-lg font-semibold mb-3">成绩矩阵</h2>
          <div className="flex gap-2 flex-wrap">
            {Object.entries(data.summary).map(([adp, s]) => (
              <div
                key={adp}
                className={
                  s.correct === s.total
                    ? "pill pill-green"
                    : s.correct > 0
                    ? "pill pill-amber"
                    : "pill pill-red"
                }
                title={`correct ${s.correct} / wrong ${s.wrong} / evasive ${s.evasive} / err ${s.error}`}
              >
                {adp} {s.correct}/{s.total}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* experiments 表格 + 行内 timeline */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Experiments（{data.results_count}）</h2>
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500 text-xs uppercase">
              <tr>
                <th className="py-2 pr-3">qid</th>
                <th className="py-2 pr-3">adapter</th>
                <th className="py-2 pr-3">判分</th>
                <th className="py-2 pr-3">召回</th>
                <th className="py-2 pr-3">LLM 延迟</th>
                <th className="py-2 pr-3">回答</th>
                <th className="py-2 pr-3 text-center">Pipeline</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((row, i) => {
                const key = `${row.adapter}__${row.qid}__${i}`;
                const pipeKey = `${row.adapter}__${row.qid}`;
                const hasPipeline = pipelineMap.has(pipeKey);
                const isExpanded = expanded === key;
                return (
                  <Row
                    key={key}
                    row={row}
                    rowKey={key}
                    isExpanded={isExpanded}
                    hasPipeline={hasPipeline}
                    onToggle={() => setExpanded(isExpanded ? null : key)}
                    group={data.name}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Row({
  row,
  rowKey,
  isExpanded,
  hasPipeline,
  onToggle,
  group,
}: {
  row: ExperimentRow;
  rowKey: string;
  isExpanded: boolean;
  hasPipeline: boolean;
  onToggle: () => void;
  group: string;
}) {
  const [pipeline, setPipeline] = useState<PipelineResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isExpanded && !pipeline && hasPipeline) {
      setLoading(true);
      api
        .getPipeline(group, row.adapter, row.qid)
        .then((p) => setPipeline(p))
        .finally(() => setLoading(false));
    }
  }, [isExpanded, pipeline, hasPipeline, group, row.adapter, row.qid]);

  const verdict = row.judge_verdict;
  const verdictCls =
    verdict === "correct"
      ? "pill pill-green"
      : verdict === "wrong"
      ? "pill pill-red"
      : verdict === "evasive"
      ? "pill pill-amber"
      : "pill pill-zinc";

  return (
    <>
      <tr className="border-t border-slate-200 hover:bg-slate-50">
        <td className="py-2 pr-3 font-mono">{row.qid}</td>
        <td className="py-2 pr-3">{row.adapter}</td>
        <td className="py-2 pr-3">
          <span className={verdictCls}>{verdict ?? "-"}</span>
        </td>
        <td className="py-2 pr-3">{row.recalled_count ?? 0}</td>
        <td className="py-2 pr-3 text-xs text-slate-600">
          {row.llm_latency_ms ? `${(row.llm_latency_ms / 1000).toFixed(2)}s` : "-"}
        </td>
        <td className="py-2 pr-3 text-xs text-slate-700">
          <span className="italic">{(row.answer ?? "").slice(0, 60)}{(row.answer ?? "").length > 60 ? "…" : ""}</span>
        </td>
        <td className="py-2 pr-3 text-center">
          {hasPipeline ? (
            <button
              type="button"
              onClick={onToggle}
              className="text-emerald-700 hover:text-emerald-800 text-xs"
            >
              {isExpanded ? "▼ 收起" : "▶ 展开"}
            </button>
          ) : (
            <span className="text-slate-400 text-xs" title="harness 跑时没带 --trace">—</span>
          )}
        </td>
      </tr>
      {isExpanded && (
        <tr className="border-b border-slate-200">
          <td colSpan={7} className="bg-slate-50 p-4">
            {loading ? (
              <div className="text-slate-500 text-sm">载入 pipeline…</div>
            ) : pipeline ? (
              <div>
                <div className="text-xs text-slate-500 mb-3">
                  来源：<code className="font-mono">{pipeline.source}</code>
                  {pipeline.note && <span className="ml-2 text-amber-700">⚠ {pipeline.note}</span>}
                </div>
                <PipelineTimeline events={pipeline.events as SpanEvent[]} />
              </div>
            ) : (
              <div className="text-slate-500 text-sm">无数据</div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
