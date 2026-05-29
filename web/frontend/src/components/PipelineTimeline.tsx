/**
 * Pipeline 瀑布图组件（B1.4 简化版）
 *
 * 设计：
 * - CSS Grid 瀑布图（不上 reactflow，更轻量 + 浅色清新风格）
 * - 把 ENTER/EXIT events 配对成 span 行
 * - ATTR events 合并到对应 span 的 attrs
 * - 缩进表示嵌套深度
 * - 横条宽度按 duration_ms 相对总时长比例
 * - 颜色按 span name 前缀（adapter.* / llm.* / embedding.* / run_root）
 *
 * 数据契约：见 lib/types.ts SpanEvent
 */
import type { SpanEvent } from "../lib/types";
import { useMemo, useState } from "react";

interface SpanRow {
  span_id: string;
  parent_id: string | null;
  name: string;
  ts_enter: number;
  ts_exit?: number;
  duration_ms?: number;
  attrs: Record<string, unknown>;
  depth: number;
  has_error: boolean;
}

function eventsToSpans(events: SpanEvent[]): SpanRow[] {
  const enterMap = new Map<string, SpanEvent>();
  const exitMap = new Map<string, SpanEvent>();
  const attrMap = new Map<string, Record<string, unknown>>();

  for (const e of events) {
    if (e.type === "ENTER") enterMap.set(e.span_id, e);
    else if (e.type === "EXIT") exitMap.set(e.span_id, e);
    else if (e.type === "ATTR") {
      const cur = attrMap.get(e.span_id) ?? {};
      const { event_id, span_id, parent_id, name, type, ts, ...attrs } = e;
      attrMap.set(e.span_id, { ...cur, ...attrs });
    }
  }

  // 计算 depth：从 parent_id 向上递归
  const depthCache = new Map<string, number>();
  function getDepth(span_id: string | null, visited = new Set<string>()): number {
    if (!span_id) return 0;
    if (depthCache.has(span_id)) return depthCache.get(span_id)!;
    if (visited.has(span_id)) return 0;
    visited.add(span_id);
    const enter = enterMap.get(span_id);
    if (!enter || enter.parent_id === null) {
      depthCache.set(span_id, 0);
      return 0;
    }
    const d = getDepth(enter.parent_id, visited) + 1;
    depthCache.set(span_id, d);
    return d;
  }

  const rows: SpanRow[] = [];
  for (const [span_id, enter] of enterMap) {
    const exit = exitMap.get(span_id);
    const attrs = { ...(enter as unknown as Record<string, unknown>), ...(exit ?? {}), ...(attrMap.get(span_id) ?? {}) };
    // 清掉冗余字段
    delete attrs.event_id;
    delete attrs.span_id;
    delete attrs.parent_id;
    delete attrs.name;
    delete attrs.type;
    delete attrs.ts;
    rows.push({
      span_id,
      parent_id: enter.parent_id,
      name: enter.name,
      ts_enter: enter.ts,
      ts_exit: exit?.ts,
      duration_ms: exit?.duration_ms,
      attrs,
      depth: getDepth(enter.parent_id),
      has_error: !!(exit?.attrs as Record<string, unknown> | undefined)?.error || !!attrs.error,
    });
  }
  rows.sort((a, b) => a.ts_enter - b.ts_enter);
  return rows;
}

function spanColor(name: string, hasError: boolean): string {
  if (hasError) return "bg-rose-100 border-rose-300 text-rose-800";
  if (name === "run_root") return "bg-slate-100 border-slate-300 text-slate-800";
  if (name.startsWith("adapter.")) return "bg-sky-50 border-sky-200 text-sky-800";
  if (name.startsWith("llm.")) return "bg-violet-50 border-violet-200 text-violet-800";
  if (name.startsWith("embedding.")) return "bg-emerald-50 border-emerald-200 text-emerald-800";
  return "bg-slate-50 border-slate-200 text-slate-700";
}

function durationLabel(ms?: number): string {
  if (ms == null) return "—";
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${ms.toFixed(1)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export default function PipelineTimeline({ events }: { events: SpanEvent[] }) {
  const spans = useMemo(() => eventsToSpans(events), [events]);
  const [expandedSpan, setExpandedSpan] = useState<string | null>(null);

  if (spans.length === 0) {
    return <div className="text-slate-500 text-sm">没有 trace 数据（这个 run 没用 --trace 跑）</div>;
  }

  // 计算总时长用于横条宽度
  const minTs = Math.min(...spans.map((s) => s.ts_enter));
  const maxTs = Math.max(...spans.map((s) => s.ts_exit ?? s.ts_enter));
  const totalMs = (maxTs - minTs) * 1000;

  return (
    <div className="space-y-1.5">
      {/* 总览 bar */}
      <div className="flex items-center justify-between text-xs text-slate-500 mb-2">
        <span>{spans.length} spans · 总耗时 {durationLabel(totalMs)}</span>
        <span className="flex gap-2">
          <Legend color="sky" label="adapter" />
          <Legend color="violet" label="llm" />
          <Legend color="emerald" label="embedding" />
          <Legend color="rose" label="error" />
        </span>
      </div>

      {spans.map((s) => {
        const startOffset = ((s.ts_enter - minTs) * 1000) / Math.max(totalMs, 1);
        const widthPct = ((s.duration_ms ?? 0) / Math.max(totalMs, 1));
        const isExpanded = expandedSpan === s.span_id;
        const colorCls = spanColor(s.name, s.has_error);
        return (
          <div key={s.span_id}>
            <button
              type="button"
              onClick={() => setExpandedSpan(isExpanded ? null : s.span_id)}
              className="w-full text-left text-sm"
            >
              <div className="flex items-center gap-2">
                {/* 缩进 */}
                <div style={{ width: `${s.depth * 16}px` }} className="flex-shrink-0" />
                {/* span 主体（含名称 + 横条）*/}
                <div className={`flex-1 rounded border px-2 py-1 ${colorCls} hover:shadow-sm transition`}>
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-xs font-medium">{s.name}</span>
                    <span className="text-[10px] font-mono opacity-70">{durationLabel(s.duration_ms)}</span>
                  </div>
                  {/* 时长横条（相对全局时长）*/}
                  {(s.duration_ms ?? 0) > 0 && (
                    <div className="mt-1 h-1 bg-white/50 rounded relative overflow-hidden">
                      <div
                        className="absolute h-full bg-current opacity-40"
                        style={{
                          left: `${Math.min(startOffset * 100, 100)}%`,
                          width: `${Math.max(widthPct * 100, 0.5)}%`,
                        }}
                      />
                    </div>
                  )}
                </div>
              </div>
            </button>
            {isExpanded && (
              <div className="ml-6 mt-1 mb-2 pl-4 border-l-2 border-slate-200">
                <SpanDetail span={s} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SpanDetail({ span }: { span: SpanRow }) {
  const entries = Object.entries(span.attrs).filter(([k]) => !k.startsWith("_"));
  if (entries.length === 0) {
    return <div className="text-xs text-slate-500 py-1">（无附加属性）</div>;
  }
  return (
    <dl className="text-xs space-y-1 py-1">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[140px_1fr] gap-2">
          <dt className="text-slate-500 font-mono">{k}</dt>
          <dd className="text-slate-800 break-all">
            {typeof v === "object" ? <code>{JSON.stringify(v)}</code> : <span>{String(v)}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  const cls = `inline-block w-2 h-2 rounded-full bg-${color}-400`;
  return (
    <span className="flex items-center gap-1">
      <span className={cls} />
      <span className="text-[10px]">{label}</span>
    </span>
  );
}
