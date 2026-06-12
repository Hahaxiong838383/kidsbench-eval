"""MemMachine adapter 契约测试（不需起真 server）。

覆盖（Phase 0 + codex 对抗审钉死的必测项）：
- 真值保存：原文 verbatim 回传 + native turn_id 溯源
- STM∪LTM 运行中并集语义（codex P0：排序/去重，不只是重启后召回）
- 幂等查重（非幂等系统的 sidecar 防护）
- 虚拟时钟：timestamp 注入落地
- 物理清场 + typed 错误不吞
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from kidsbench.adapters import MemMachineAdapter
from kidsbench.contract import AdapterError, ReadOpts, Turn


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeMemMachineSession:
    """模拟 MemMachine HTTP v2：write 入 STM+LTM，search 回 STM 优先去重。"""

    def __init__(self):
        self._episodes: dict[str, list[dict]] = {}  # project_id -> episodes
        self._uid = 0
        self.deleted: list[str] = []

    def post(self, url, json=None, timeout=None):
        body = json or {}
        proj = body.get("project_id", "")
        if url.endswith("/memories"):
            results = []
            for msg in body.get("messages", []):
                self._uid += 1
                ep = {
                    "content": msg["content"],
                    "producer_id": msg.get("producer", "user"),
                    "metadata": msg.get("metadata", {}),
                    "created_at": msg.get("timestamp"),
                    "uid": str(self._uid),
                }
                self._episodes.setdefault(proj, []).append(ep)
                results.append({"uid": str(self._uid)})
            return _FakeResp(200, {"results": results})
        if url.endswith("/memories/search"):
            if proj in self.deleted:
                return _FakeResp(500, {"detail": "SessionDeletedError"})
            query = body.get("query", "")
            eps = self._episodes.get(proj, [])
            hits = [e for e in eps if query and any(c in e["content"] for c in query) ] or eps
            # 模拟 STM 优先去重：最近 2 条进 STM（无 score），更早的进 LTM（带 score）
            stm = [{**e} for e in hits[-2:]]
            ltm = [{**e, "score": 0.5 + i * 0.1} for i, e in enumerate(hits[:-2])]
            return _FakeResp(200, {"content": {"episodic_memory": {
                "short_term_memory": {"episodes": stm},
                "long_term_memory": {"episodes": ltm},
            }}})
        if url.endswith("/projects/delete"):
            self.deleted.append(proj)
            self._episodes.pop(proj, None)
            return _FakeResp(204, {})
        return _FakeResp(404, {"detail": "unknown"})

    def close(self):
        pass


def _turn(tid="t_001", text="我家的布偶猫叫团子", ts=None):
    return Turn(turn_id=tid, session_id="s1", role="user", text=text,
                timestamp=ts if ts is not None else time.time())


def _adapter():
    return MemMachineAdapter(session=_FakeMemMachineSession())


def test_write_read_verbatim_native_traceback():
    a = _adapter()
    a.write("u1", _turn())
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    assert rr.memories, "应召回写入的 episode"
    m = rr.memories[0]
    assert m.text == "我家的布偶猫叫团子"  # 原文 verbatim
    assert m.text_nature == "verbatim"
    assert m.source_turn_ids == ["t_001"]  # native 溯源
    assert m.provenance_mode == "native"


def test_stm_ltm_union_dedup_running():
    """codex P0：运行中取 STM∪LTM 并集，按 uid 去重不重复，有 score 优先排序。"""
    a = _adapter()
    for i in range(4):
        a.write("u1", _turn(tid=f"t_{i}", text=f"记忆条目{i}团子"))
    rr = a.read("u1", "团子", ReadOpts(top_k=10))
    uids = [m.memory_id for m in rr.memories]
    assert len(uids) == len(set(uids)), "STM∪LTM 并集不能有重复 uid"
    assert len(rr.memories) == 4, "4 条写入应全部召回（并集取全）"
    # 带 score 的（LTM）排在前
    scores = [m.score for m in rr.memories]
    assert scores == sorted(scores, reverse=True)


def test_virtual_clock_injection():
    a = _adapter()
    a.write("u1", _turn(ts=1717574400.0))  # 2024-06-05
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    assert rr.memories[0].timestamp is not None
    # 落地时间应接近注入值（容忍时区/精度）
    assert abs(rr.memories[0].timestamp - 1717574400.0) < 86400


def test_idempotent_dedup():
    a = _adapter()
    a.write("u1", _turn())
    s2 = a.write("u1", _turn())  # 同 turn_id 重写
    assert s2.raw.get("deduplicated") is True
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    assert len(rr.memories) == 1  # 底层只写一次


def test_physical_clear():
    a = _adapter()
    a.write("u1", _turn())
    a.clear("u1")
    # 清场后 search 抛 SessionDeletedError → AdapterError（fail-fast 不吞）
    with pytest.raises(AdapterError):
        a.read("u1", "团子", ReadOpts(top_k=5))


def test_empty_user_id_rejected():
    a = _adapter()
    with pytest.raises(AdapterError):
        a.write("", _turn())


def test_capability_honesty():
    a = _adapter()
    prof = a.get_capability_profile()
    by = {c.feature: c for c in prof.capabilities}
    assert by["turn_id_traceback"].level == "native"
    assert by["physical_clear"].level == "native"
    assert by["cost_accounting"].level == "declared"  # 不吹牛
