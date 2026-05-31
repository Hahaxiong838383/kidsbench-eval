/**
 * 实时跑题监控页（B1.3）：/live
 *
 * 功能：
 * - 顶部列出内存里的 run（活跃 + 已完成），点击订阅
 * - EventSource 订阅 /api/run/{id}/stream，实时累积 span events
 * - 复用 PipelineTimeline 渲染瀑布图（进行中的 span 显示「—」时长）
 * - complete 信号到达后停止订阅，标记完成
 *
 * 用法：
 *   harness 跑题时带 --trace-http http://<backend>/api/run/{run_id}/event
 *   本页选对应 run_id 实时看
 */
import { useEffect, useMemo, useRef, useState } from "react";
import PipelineTimeline from "../components/PipelineTimeline";
import { fetchLiveRuns, subscribeRun, type LiveRunInfo, type RunSubscription } from "../lib/sse";
import type { SpanEvent } from "../lib/types";

type ConnState = "idle" | "connecting" | "live" | "complete" | "error";

export default function LiveRun() {
  const [runs, setRuns] = useState<LiveRunInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<SpanEvent[]>([]);
  const [conn, setConn] = useState<ConnState>("idle");
  const subRef = useRef<RunSubscription | null>(null);

  // 轮询 run 列表（活跃 run 会动态出现）
  useEffect(() => {
    let alive = true;
    const poll = () => {
      fetchLiveRuns()
        .then((d) => {
          if (alive) setRuns(d.runs);
        })
        .catch(() => {});
    };
    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // 订阅选中的 run
  useEffect(() => {
    // 清理旧订阅
    subRef.current?.close();
    subRef.current = null;
    setEvents([]);
    if (!selected) {
      setConn("idle");
      return;
    }
    setConn("connecting");
    const sub = subscribeRun(selected, {
      onOpen: () => setConn("live"),
      onSpan: (e) => setEvents((cur) => [...cur, e]),
      onComplete: () => setConn("complete"),
      onError: () => setConn("error"),
    });
    subRef.current = sub;
    return () => {
      sub.close();
      subRef.current = null;
    };
  }, [selected]);

  const connBadge = useMemo(() => {
    switch (conn) {
      case "live":
        return <span className="pill pill-green">● 实时</span>;
      case "connecting":
        return <span className="pill pill-amber">连接中…</span>;
      case "complete":
        return <span className="pill pill-blue">✓ 已完成</span>;
      case "error":
        return <span className="pill pill-red">连接错误（自动重连中）</span>;
      default:
        return <span className="pill pill-zinc">未选择</span>;
    }
  }, [conn]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">实时跑题监控</h1>
        <p className="text-sm text-slate-500 mt-1">
          harness 跑题时带{" "}
          <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded text-xs">
            --trace-http http://&lt;backend&gt;/api/run/{"{run_id}"}/event
          </code>{" "}
          ，本页实时看 pipeline 瀑布图滚动出现。
        </p>
      </header>

      {/* run 选择器 */}
      <section className="card">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">内存中的 Run（{runs.length}）</h2>
          {connBadge}
        </div>
        {runs.length === 0 ? (
          <div className="text-sm text-slate-500">
            还没有 run。在 Air 上跑{" "}
            <code className="font-mono text-xs">--trace-http</code> 后会自动出现。
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {runs.map((r) => (
              <button
                key={r.run_id}
                type="button"
                onClick={() => setSelected(r.run_id)}
                className={`text-left rounded border px-3 py-2 text-sm transition ${
                  selected === r.run_id
                    ? "border-emerald-400 bg-emerald-50"
                    : "border-slate-200 bg-white hover:border-slate-300"
                }`}
              >
                <div className="font-mono text-xs">{r.run_id}</div>
                <div className="text-[10px] text-slate-500 mt-0.5">
                  {r.event_count} events ·{" "}
                  {r.completed ? (
                    <span className="text-blue-600">已完成</span>
                  ) : (
                    <span className="text-emerald-600">● 进行中</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* 实时 pipeline */}
      {selected && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">
              Pipeline <code className="font-mono text-sm text-emerald-700">{selected}</code>
            </h2>
            <span className="text-xs text-slate-500">{events.length} events 已接收</span>
          </div>
          <div className="card">
            {events.length === 0 ? (
              <div className="text-sm text-slate-500">
                {conn === "connecting" ? "连接中…" : "等待事件…"}
              </div>
            ) : (
              <PipelineTimeline events={events} />
            )}
          </div>
        </section>
      )}
    </div>
  );
}
