/**
 * SSE 订阅 client（B1.3）
 *
 * 浏览器 EventSource 原生支持：
 * - 自动重连
 * - 重连时自动发 Last-Event-ID header（后端据此续传，不丢事件）
 * - 同源请求自动带 Basic Auth 凭据（浏览器已认证）
 *
 * 后端 SSE 帧格式（events.py _format_sse）：
 *   id: <event_id>
 *   event: span        ← span 数据
 *   data: <json>
 *
 *   event: complete    ← run 结束信号
 *   data: {}
 */
import type { SpanEvent } from "./types";

export interface RunSubscription {
  /** 关闭订阅 */
  close: () => void;
}

export function subscribeRun(
  runId: string,
  handlers: {
    onSpan: (event: SpanEvent) => void;
    onComplete?: () => void;
    onError?: (err: Event) => void;
    onOpen?: () => void;
  },
): RunSubscription {
  const url = `/api/run/${encodeURIComponent(runId)}/stream`;
  const es = new EventSource(url);

  es.addEventListener("open", () => handlers.onOpen?.());

  es.addEventListener("span", (ev) => {
    try {
      const data = JSON.parse((ev as MessageEvent).data) as SpanEvent;
      handlers.onSpan(data);
    } catch {
      // 忽略坏帧
    }
  });

  es.addEventListener("complete", () => {
    handlers.onComplete?.();
    es.close();
  });

  es.onerror = (err) => {
    // EventSource 会自动重连；只在彻底失败时通知
    handlers.onError?.(err);
  };

  return {
    close: () => es.close(),
  };
}

/** 拉取活跃 / 历史 run 列表 */
export interface LiveRunInfo {
  run_id: string;
  event_count: number;
  completed: boolean;
  first_seen: number | null;
}

export async function fetchLiveRuns(): Promise<{
  runs: LiveRunInfo[];
  active: string[];
  count: number;
}> {
  const res = await fetch("/api/run/live");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
