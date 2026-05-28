from __future__ import annotations

import threading
import time

import pytest

from kidsbench.contract import AdapterError, WriteStats
from kidsbench.middleware.metrics import METRICS, metrics_context, track_metrics


class DummyAdapter:
    name = "dummy"

    @track_metrics(method="write")
    def write(self, user_id: str, ok: bool = True) -> WriteStats:
        if not ok:
            raise AdapterError("boom")
        time.sleep(0.005)
        return WriteStats(success=True, latency_ms=0.0, cost_token=7)


def setup_function() -> None:
    METRICS.reset()


def test_track_metrics_success_populates_latency() -> None:
    adapter = DummyAdapter()
    stats = adapter.write("u1")
    assert stats.latency_ms > 0

    snap = METRICS.snapshot("dummy", "u1")
    assert snap["total_calls"] == 1
    assert snap["success_calls"] == 1
    assert snap["error_calls"] == 0
    assert snap["total_cost_token"] == 7
    assert snap["methods"]["write"]["calls"] == 1


def test_track_metrics_error_path_records_and_reraises() -> None:
    adapter = DummyAdapter()
    with pytest.raises(AdapterError):
        adapter.write("u2", ok=False)

    snap = METRICS.snapshot("dummy", "u2")
    assert snap["total_calls"] == 1
    assert snap["error_calls"] == 1
    assert snap["last_error_class"] == "AdapterError"


def test_metrics_context_overrides_adapter_name_and_run_id() -> None:
    adapter = DummyAdapter()
    with metrics_context(adapter_name="from_ctx", run_id="run-1"):
        adapter.write("u3")

    snap = METRICS.snapshot("from_ctx", "u3")
    assert snap["total_calls"] == 1
    assert snap["run_id"] == "run-1"


def test_metrics_concurrent_records() -> None:
    adapter = DummyAdapter()

    def worker() -> None:
        adapter.write("u4")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    snap = METRICS.snapshot("dummy", "u4")
    assert snap["total_calls"] == 8
