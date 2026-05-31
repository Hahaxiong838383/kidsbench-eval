"""SSE 实时事件流测试（B1.2）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def events_client(monkeypatch):
    """独立 client + 清空 event bus（避免测试间污染）。"""
    import importlib

    from app import events as _events

    importlib.reload(_events)
    from app import main as _main

    importlib.reload(_main)
    return TestClient(_main.app)


def test_ingest_single_event(events_client):
    r = events_client.post(
        "/api/run/r_test1/event",
        json={"event_id": 1, "span_id": "sp-1", "name": "adapter.write", "type": "ENTER"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["received"] == 1
    assert body["stored"] == 1


def test_ingest_batch_events(events_client):
    batch = [
        {"event_id": 1, "name": "a", "type": "ENTER"},
        {"event_id": 2, "name": "b", "type": "EXIT"},
    ]
    r = events_client.post("/api/run/r_test2/event", json=batch)
    assert r.status_code == 200
    assert r.json()["received"] == 2


def test_ingest_invalid_body(events_client):
    # Gemini D.1：畸形数据返回 400（触发 Air 重试），不再 200
    r = events_client.post("/api/run/r_x/event", json="not a dict or list")
    assert r.status_code == 400


def test_live_runs_lists_ingested(events_client):
    events_client.post("/api/run/r_live1/event", json={"event_id": 1, "name": "x"})
    events_client.post("/api/run/r_live2/event", json={"event_id": 1, "name": "y"})
    r = events_client.get("/api/run/live")
    assert r.status_code == 200
    body = r.json()
    run_ids = {x["run_id"] for x in body["runs"]}
    assert "r_live1" in run_ids
    assert "r_live2" in run_ids
    # 都未 complete → 都 active
    assert "r_live1" in body["active"]


def test_complete_marks_run(events_client):
    events_client.post("/api/run/r_done/event", json={"event_id": 1, "name": "x"})
    r = events_client.post("/api/run/r_done/complete")
    assert r.status_code == 200
    live = events_client.get("/api/run/live").json()
    done = next(x for x in live["runs"] if x["run_id"] == "r_done")
    assert done["completed"] is True
    assert "r_done" not in live["active"]


def test_stream_replays_buffer_then_complete(events_client):
    """已完成的 run：stream 重放历史后立即发 complete 并结束。"""
    events_client.post(
        "/api/run/r_replay/event",
        json=[
            {"event_id": 1, "span_id": "s1", "name": "run_root", "type": "ENTER"},
            {"event_id": 2, "span_id": "s2", "name": "adapter.write", "type": "EXIT"},
        ],
    )
    events_client.post("/api/run/r_replay/complete")

    # stream 一个已完成的 run，应该拿到 2 个 span event + 1 个 complete
    with events_client.stream("GET", "/api/run/r_replay/stream") as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    # 解析 SSE 帧
    assert "event: span" in text
    assert '"name":"run_root"' in text
    assert '"name":"adapter.write"' in text
    assert "event: complete" in text


def test_stream_last_event_id_replay_filter(events_client):
    """last_event_id 续传：只重放 event_id > N 的。"""
    events_client.post(
        "/api/run/r_resume/event",
        json=[
            {"event_id": 1, "name": "first"},
            {"event_id": 2, "name": "second"},
            {"event_id": 3, "name": "third"},
        ],
    )
    events_client.post("/api/run/r_resume/complete")

    with events_client.stream(
        "GET", "/api/run/r_resume/stream?last_event_id=1"
    ) as resp:
        text = "".join(resp.iter_text())
    assert '"name":"first"' not in text  # event_id=1 被过滤
    assert '"name":"second"' in text
    assert '"name":"third"' in text


def test_sse_frame_format(events_client):
    """SSE 帧含 id / event / data 三字段。"""
    events_client.post(
        "/api/run/r_fmt/event", json={"event_id": 42, "name": "test"}
    )
    events_client.post("/api/run/r_fmt/complete")
    with events_client.stream("GET", "/api/run/r_fmt/stream") as resp:
        text = "".join(resp.iter_text())
    assert "id: 42" in text
    assert "event: span" in text
    # data 行是合法 JSON
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: {}":
            parsed = json.loads(line[len("data: "):])
            assert parsed["event_id"] == 42


def test_stream_unknown_run_empty(events_client):
    """订阅不存在的 run（且未 complete）→ 重放空，挂起监听。

    用 disconnect 模拟：TestClient 不便测长连接，这里只验证重放阶段不报错。
    """
    # 不 complete 的 unknown run 会挂起，测试用 complete 让它快速结束
    events_client.post("/api/run/r_unknown/complete")
    with events_client.stream("GET", "/api/run/r_unknown/stream") as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "event: complete" in text


def test_last_event_id_non_numeric_no_422(events_client):
    """Gemini C.1：非数字 Last-Event-ID 不应触发 422，容错为 0。"""
    events_client.post("/api/run/r_c1/event", json={"event_id": 1, "name": "x"})
    events_client.post("/api/run/r_c1/complete")
    # query param 非数字
    with events_client.stream(
        "GET", "/api/run/r_c1/stream?last_event_id=abc"
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert '"name":"x"' in text  # 容错为 0 → 重放全部


def test_subscription_lagging_self_heal():
    """Gemini B.2：队列满标记 lagging（单元级测 RunEventBus）。"""
    import asyncio

    from app.events import SUBSCRIBER_QUEUE_SIZE, _RunEventBus

    async def run():
        bus = _RunEventBus()
        sub = await bus.subscribe("r_lag")
        # 灌爆队列（超过 maxsize）
        big_batch = [{"event_id": i, "name": f"e{i}"} for i in range(SUBSCRIBER_QUEUE_SIZE + 50)]
        await bus.publish("r_lag", big_batch)
        return sub.lagging

    assert asyncio.run(run()) is True


def test_evict_signals_subscribers():
    """Gemini A.1：LRU 淘汰正在订阅的 run 时推 __evicted__ 信号。"""
    import asyncio

    from app.events import MAX_RUNS, _RunEventBus

    async def run():
        bus = _RunEventBus()
        # 订阅第一个 run
        sub = await bus.subscribe("r_evict_me")
        await bus.publish("r_evict_me", [{"event_id": 1, "name": "x"}])
        # 灌满 MAX_RUNS 个新 run 触发淘汰
        for i in range(MAX_RUNS + 5):
            await bus.publish(f"r_filler_{i}", [{"event_id": 1, "name": "f"}])
        # r_evict_me 应已被淘汰，sub.queue 里应有 __evicted__
        found_evicted = False
        while not sub.queue.empty():
            item = sub.queue.get_nowait()
            if isinstance(item, dict) and item.get("__evicted__"):
                found_evicted = True
        return found_evicted

    assert asyncio.run(run()) is True
