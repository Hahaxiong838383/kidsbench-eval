"""Span event 双通道导出。

通道：
1. JsonlExporter — 写本地 pipeline.jsonl（冷数据归档）
   - 写临时目录（atomically 切换到正式目录由 harness 控制）
   - append-only，每行一个 event
   - Gemini A.3：原子性由 write tmp + mv 解决

2. HttpExporter — POST 实时事件到 QNAP backend（实时热数据）
   - 后台线程 + Queue 异步发送，主流程不阻塞
   - 失败重试 3 次后放弃（jsonl 兜底）
   - Gemini A.1：实时通道不依赖 rsync

3. MultiExporter — 同时跑多个 exporter（默认 jsonl + http）

按 CLAUDE.md：
- 错误处理：所有 export 失败不抛错到 _emit
- 不可变：Exporter 创建后只读
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

from .span import SpanEvent, _set_exporter_internal

_log = logging.getLogger(__name__)


# ============================================================
# 抽象基类
# ============================================================

class Exporter(ABC):
    @abstractmethod
    def export(self, event: SpanEvent) -> None: ...

    def flush(self) -> None:
        """harness finalize 时调，blocking 等所有 pending events 落地。默认 no-op。"""
        pass


# ============================================================
# NullExporter — 默认（不 init trace 时）
# ============================================================

class NullExporter(Exporter):
    def export(self, event: SpanEvent) -> None:
        pass


# ============================================================
# JsonlExporter — 本地 jsonl 文件
# ============================================================

class JsonlExporter(Exporter):
    """每行一个 JSON event 的 append-only 文件。

    并发：用 threading.Lock 保护写。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # touch 文件
        self.path.touch(exist_ok=True)
        self._lock = threading.Lock()

    def export(self, event: SpanEvent) -> None:
        try:
            line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            _log.warning("event 序列化失败 span_id=%s: %s", event.span_id, exc)
            return
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as exc:
                _log.warning("jsonl 写入失败 %s: %s", self.path, exc)


# ============================================================
# HttpExporter — 后台线程异步 POST
# ============================================================

class HttpExporter(Exporter):
    """实时把 event POST 到 backend `/api/run/{run_id}/event`。

    设计：
    - 主线程 export() 只把 event 塞 queue，立即返回（不阻塞 harness）
    - 后台 daemon 线程从 queue 拿 event，批量 POST
    - 单个 event 失败重试 3 次（指数退避），仍失败就丢弃（jsonl 兜底）
    - flush() 等 queue 排空

    参数：
        endpoint_tpl: URL 模板，含 {run_id} 占位，如 "http://qnap:18001/api/run/{run_id}/event"
        run_id_getter: 调用时拿当前 run_id 的函数
        batch_size: 单次 POST 携带的 event 上限
        flush_interval: 后台线程 idle 时强制 flush 间隔（秒）
    """

    def __init__(
        self,
        endpoint_tpl: str,
        run_id_getter: callable,
        *,
        batch_size: int = 16,
        flush_interval: float = 0.5,
        max_retries: int = 3,
        timeout: float = 3.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint_tpl = endpoint_tpl
        self.run_id_getter = run_id_getter
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries
        self.timeout = timeout
        # 公网经 nginx Basic Auth 时传 {"Authorization": "Basic ..."}
        self.extra_headers = dict(extra_headers) if extra_headers else {}
        self._queue: queue.Queue = queue.Queue(maxsize=10000)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run_loop, daemon=True, name="trace-http-exporter")
        self._worker.start()

    def export(self, event: SpanEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            _log.warning("trace HTTP exporter queue 满，丢弃 event %s", event.span_id)

    def flush(self) -> None:
        # 等 queue 排空（最多 5s）
        deadline = time.monotonic() + 5.0
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)

    def stop(self) -> None:
        self._stop.set()
        self._worker.join(timeout=2.0)

    def _run_loop(self) -> None:
        batch: list[SpanEvent] = []
        last_flush = time.monotonic()
        while not self._stop.is_set():
            try:
                # 短超时 poll
                event = self._queue.get(timeout=0.1)
                batch.append(event)
                if len(batch) >= self.batch_size:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.monotonic()
            except queue.Empty:
                pass
            if batch and (time.monotonic() - last_flush) > self.flush_interval:
                self._flush_batch(batch)
                batch = []
                last_flush = time.monotonic()
        # 退出前 flush 残留
        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list[SpanEvent]) -> None:
        run_id = self.run_id_getter()
        if not run_id:
            return
        url = self.endpoint_tpl.format(run_id=run_id)
        payload = json.dumps([e.to_dict() for e in batch], ensure_ascii=False).encode("utf-8")
        for attempt in range(self.max_retries):
            try:
                headers = {"Content-Type": "application/json", **self.extra_headers}
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                wait = 0.2 * (2 ** attempt)
                _log.debug("HTTP exporter attempt %d failed: %s; retry in %.1fs", attempt + 1, exc, wait)
                time.sleep(wait)
            except Exception as exc:  # noqa: BLE001
                _log.warning("HTTP exporter 未知错误：%s", exc)
                return
        _log.warning("HTTP exporter %d events 失败 %d 次，放弃（jsonl 兜底）", len(batch), self.max_retries)


# ============================================================
# MultiExporter — 同时多通道
# ============================================================

class MultiExporter(Exporter):
    def __init__(self, exporters: list[Exporter]) -> None:
        self.exporters = list(exporters)

    def export(self, event: SpanEvent) -> None:
        for e in self.exporters:
            try:
                e.export(event)
            except Exception as exc:  # noqa: BLE001
                _log.warning("sub-exporter %r 失败：%s", type(e).__name__, exc)

    def flush(self) -> None:
        for e in self.exporters:
            try:
                e.flush()
            except Exception as exc:  # noqa: BLE001
                _log.warning("sub-exporter flush 失败：%s", exc)


# ============================================================
# 全局注册（避免循环 import）
# ============================================================

def set_exporter(exporter: Exporter | None) -> None:
    """注册全局 exporter。None 表示禁用（等同 NullExporter）。"""
    _set_exporter_internal(exporter if exporter is not None else NullExporter())
