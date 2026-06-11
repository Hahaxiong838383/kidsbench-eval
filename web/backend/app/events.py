"""实时事件流（B1.2）：接收 Air harness 推送的 span events，经 SSE 推前端。

设计（合 Gemini A.1 / B.2 / B.3 + high-reasoning 评审 3🔴+2🟡+2🔵 全采纳）：
- POST /api/run/{run_id}/event    Air HttpExporter 推送 span events（batch）
- POST /api/run/{run_id}/complete Air 通知 run 结束
- GET  /api/run/{run_id}/stream   前端 EventSource 订阅（SSE）
- GET  /api/run/live              列出活跃 / 历史 run

核心：内存 RunEventBus（单进程 uvicorn --workers 1）
- 每 run_id 一个 ring buffer（存 events，供 Last-Event-ID 重放）
- 每 run_id 一组 Subscription（订阅者 + lagging 健康标记）
- LRU 淘汰旧 run（淘汰时推 __evicted__ 信号让订阅协程优雅退出）

Gemini 评审修复（生产级恶劣场景）：
- 🔴 A.1 LRU 淘汰正订阅的 run → 推 __evicted__ 防协程永久挂起
- 🔴 B.2 队列满不再静默丢 → 标记 lagging + 断连，前端 EventSource 自动带
       Last-Event-ID 重连，从 buffer 无损补齐
- 🔴 C.4 "先重放后订阅"真空期丢事件 → 改"先订阅后重放 + ID 去重(last_sent_id)"
- 🟡 C.3 大批量重放阻塞 event loop → 每 100 条 await asyncio.sleep(0) 让出
- 🟡 D.1 畸形数据返回 200 让 Air 误判 → 返回 400 触发重试
- 🔵 C.1 Last-Event-ID 改 str|None 容错（非数字不再 422）
- 🔵 C.2 心跳只在空闲(TimeoutError)时发，省跨境带宽

零侵入：纯新增模块，不碰 runs.py / state.py 等已跑通代码。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict, defaultdict
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/run", tags=["run-events"])

# 内存上限（防泄漏）
MAX_RUNS = 50
MAX_EVENTS_PER_RUN = 20000
SUBSCRIBER_QUEUE_SIZE = 2000
HEARTBEAT_INTERVAL = 15.0  # 秒
POLL_TIMEOUT = 1.0  # 秒：queue.get 超时，用于周期检查断连
REPLAY_YIELD_EVERY = 100  # 重放每 N 条让出一次 event loop


def _safe_int_id(event: dict) -> int:
    """安全提取 event_id（非数字返回 0）。"""
    try:
        return int(event.get("event_id", 0))
    except (ValueError, TypeError):
        return 0


class Subscription:
    """包装订阅者队列 + 健康状态（Gemini B.2 自愈设计）。"""

    __slots__ = ("lagging", "queue")

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self.lagging: bool = False  # 消费过慢导致丢包 → 触发断连自愈


class _RunEventBus:
    """单进程内存事件总线（per run_id 缓冲 + fan-out）。"""

    def __init__(self) -> None:
        self._buffers: OrderedDict[str, list[dict]] = OrderedDict()
        self._subscribers: dict[str, set[Subscription]] = defaultdict(set)
        self._completed: set[str] = set()
        self._first_seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def publish(self, run_id: str, events: list[dict]) -> int:
        if not events:
            return 0
        async with self._lock:
            buf = self._buffers.get(run_id)
            if buf is None:
                buf = []
                self._buffers[run_id] = buf
                self._first_seen[run_id] = time.time()
                self._buffers.move_to_end(run_id)
                self._evict_locked()
            else:
                self._buffers.move_to_end(run_id)
            stored = 0
            for e in events:
                if len(buf) < MAX_EVENTS_PER_RUN:
                    buf.append(e)
                    stored += 1
            subs = list(self._subscribers.get(run_id, ()))
        # fan-out 锁外
        for sub in subs:
            if sub.lagging:
                continue
            for e in events:
                try:
                    sub.queue.put_nowait(e)
                except asyncio.QueueFull:
                    # 不静默丢：标记落后，stream 协程会断连 → 前端 EventSource
                    # 自动带 Last-Event-ID 重连，从 buffer 无损补齐
                    sub.lagging = True
                    break
        return stored

    async def mark_complete(self, run_id: str) -> None:
        async with self._lock:
            self._completed.add(run_id)
            subs = list(self._subscribers.get(run_id, ()))
        for sub in subs:
            try:
                sub.queue.put_nowait({"__complete__": True})
            except asyncio.QueueFull:
                sub.lagging = True

    async def subscribe(self, run_id: str) -> Subscription:
        sub = Subscription()
        async with self._lock:
            self._subscribers[run_id].add(sub)
        return sub

    async def unsubscribe(self, run_id: str, sub: Subscription) -> None:
        async with self._lock:
            self._subscribers.get(run_id, set()).discard(sub)
            if not self._subscribers.get(run_id):
                self._subscribers.pop(run_id, None)

    def replay(self, run_id: str, after_event_id: int) -> list[dict]:
        buf = self._buffers.get(run_id, [])
        if after_event_id <= 0:
            return list(buf)
        return [e for e in buf if _safe_int_id(e) > after_event_id]

    def is_complete(self, run_id: str) -> bool:
        return run_id in self._completed

    def snapshot(self) -> list[dict]:
        out = []
        for run_id in reversed(self._buffers.keys()):
            out.append({
                "run_id": run_id,
                "event_count": len(self._buffers.get(run_id, [])),
                "completed": run_id in self._completed,
                "first_seen": self._first_seen.get(run_id),
            })
        return out

    def _evict_locked(self) -> None:
        """LRU 淘汰最旧 run（调用方须持锁）。

        🔴 A.1：淘汰前给订阅者推 __evicted__，防协程永久挂起。
        """
        while len(self._buffers) > MAX_RUNS:
            old_id, _ = self._buffers.popitem(last=False)
            subs = self._subscribers.pop(old_id, set())
            for sub in subs:
                try:
                    sub.queue.put_nowait({"__evicted__": True})
                except asyncio.QueueFull:
                    sub.lagging = True
            self._completed.discard(old_id)
            self._first_seen.pop(old_id, None)


_bus = _RunEventBus()


def get_bus() -> _RunEventBus:
    return _bus


# ============================================================
# Endpoints
# ============================================================


@router.post("/{run_id}/event")
async def ingest_event(run_id: str, request: Request) -> dict:
    """Air harness HttpExporter 推送 span events（单个 dict 或 list[dict]）。

    🟡 D.1：畸形数据返回 400（不是 200），触发 Air HttpExporter 重试。
    """
    try:
        body: Any = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc!s}") from exc
    if isinstance(body, dict):
        events = [body]
    elif isinstance(body, list):
        events = [e for e in body if isinstance(e, dict)]
    else:
        raise HTTPException(status_code=400, detail="expect dict or list of dicts")
    stored = await _bus.publish(run_id, events)
    return {"ok": True, "received": len(events), "stored": stored}


@router.post("/{run_id}/complete")
async def complete_run(run_id: str) -> dict:
    await _bus.mark_complete(run_id)
    return {"ok": True, "run_id": run_id}


@router.get("/live")
async def live_runs() -> dict:
    snap = _bus.snapshot()
    return {
        "runs": snap,
        "active": [r["run_id"] for r in snap if not r["completed"]],
        "count": len(snap),
    }


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    last_event_id: str | None = None,
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE 端点：先订阅后重放（无真空期）+ ID 去重 + lagging 自愈。"""
    # 🔵 C.1：str 容错解析（非数字不 422）
    effective_last_id = 0
    raw_id = last_event_id or last_event_id_header
    if raw_id:
        try:
            effective_last_id = int(raw_id)
        except (ValueError, TypeError):
            effective_last_id = 0

    async def event_gen():
        # 🔴 C.4：先订阅（新事件先积压进 queue，绝不丢），再重放历史
        sub = await _bus.subscribe(run_id)
        last_sent_id = effective_last_id
        try:
            # 1. 重放历史
            history = _bus.replay(run_id, effective_last_id)
            for i, e in enumerate(history):
                yield _format_sse(e)
                eid = _safe_int_id(e)
                if eid > last_sent_id:
                    last_sent_id = eid
                # 🟡 C.3：每 100 条让出 event loop，防弱 CPU 卡死
                if i % REPLAY_YIELD_EVERY == 0:
                    await asyncio.sleep(0)

            # 已完成且队列无积压 → 直接结束
            if _bus.is_complete(run_id) and sub.queue.empty():
                yield "event: complete\ndata: {}\n\n"
                return

            # 2. 实时消费
            last_hb = time.monotonic()
            while True:
                if await request.is_disconnected():
                    break
                # 🔴 B.2：检测丢包 → 断连，前端自动重连 + Last-Event-ID 补齐
                if sub.lagging:
                    yield 'event: error\ndata: {"code":"LAGGING"}\n\n'
                    break
                try:
                    e = await asyncio.wait_for(sub.queue.get(), timeout=POLL_TIMEOUT)
                    if isinstance(e, dict) and e.get("__evicted__"):
                        yield 'event: error\ndata: {"code":"EVICTED"}\n\n'
                        break
                    if isinstance(e, dict) and e.get("__complete__"):
                        yield "event: complete\ndata: {}\n\n"
                        break
                    # 🔴 C.4：去重重放与实时重叠的事件
                    eid = _safe_int_id(e)
                    if eid <= last_sent_id and eid > 0:
                        continue
                    yield _format_sse(e)
                    if eid > last_sent_id:
                        last_sent_id = eid
                except asyncio.TimeoutError:
                    # 🔵 C.2：仅空闲时发心跳，省跨境带宽
                    now = time.monotonic()
                    if now - last_hb > HEARTBEAT_INTERVAL:
                        yield ": ping\n\n"
                        last_hb = now
        finally:
            await _bus.unsubscribe(run_id, sub)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _format_sse(event: dict) -> str:
    """格式化 SSE 帧。json.dumps 已把字符串内换行转义为 \\n 字面，不破坏帧。"""
    eid = event.get("event_id", "")
    try:
        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        data = json.dumps({"_error": "unserializable event"})
    return f"id: {eid}\nevent: span\ndata: {data}\n\n"
