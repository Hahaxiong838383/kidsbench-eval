"""Cognee adapter 契约测试（注入 mock cognee module，不需装 cognee）。

覆盖（Phase 0 + codex 对抗审钉死）：
- 多跳 read：GRAPH_COMPLETION synthesized 文本
- write→consolidate→read 时序（write_semantic_sync=declared：write 仅入库，cognify 才建图）
- content hash 幂等查重（cognee 非幂等）
- 溯源诚实（wrapped，合成无源引用）+ 清场全局 prune
- consolidate 用 ZH_PROMPT（中文强制）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from kidsbench.adapters import CogneeAdapter
from kidsbench.contract import AdapterError, ReadOpts, Turn


class _SearchType:
    GRAPH_COMPLETION = "GRAPH_COMPLETION"
    CHUNKS = "CHUNKS"


class _FakePrune:
    def __init__(self, parent):
        self._p = parent

    async def prune_data(self):
        self._p._added.clear()
        self._p._graph.clear()

    async def prune_system(self, metadata=False, **kw):
        self._p._pruned_system = True


class _FakeCognee:
    """模拟 cognee 异步 API：add 入库、cognify 建图（记录 custom_prompt）、search 多跳。"""

    def __init__(self):
        self._added: list[tuple] = []
        self._graph: list[str] = []
        self._pruned_system = False
        self.last_custom_prompt = None
        self.last_depth = None
        self.SearchType = _SearchType
        self.prune = _FakePrune(self)

    async def add(self, text, dataset_name, node_set=None):
        self._added.append((text, dataset_name, tuple(node_set or [])))

    async def cognify(self, datasets, custom_prompt=None, **kw):
        self.last_custom_prompt = custom_prompt
        # 建图：把入库文本变成「图谱事实」
        self._graph = [t for (t, _d, _n) in self._added]

    async def search(self, query, query_type=None, top_k=5, neighborhood_depth=None,
                     datasets=None, **kw):
        self.last_depth = neighborhood_depth
        if not self._graph:
            return []  # 未 cognify 无图谱
        # 多跳合成：返回含 query 关键字相关的合成答案
        hits = [g for g in self._graph if any(c in g for c in query)] or self._graph
        return [f"根据知识图谱：{hits[0]}"]


# 让 adapter 的 SearchType import 走 mock：注入 cognee_module 时 _search_type 需另设
class _TestCogneeAdapter(CogneeAdapter):
    def _ensure_cognee(self):
        if self._search_type is None:
            self._search_type = self._cognee.SearchType
        return self._cognee


def _turn(tid="t_001", text="小川养了布偶猫团子，喜欢吃冻干三文鱼", ts=None):
    return Turn(turn_id=tid, session_id="s1", role="user", text=text,
                timestamp=ts if ts is not None else time.time())


def _adapter():
    return _TestCogneeAdapter(cognee_module=_FakeCognee())


def test_write_consolidate_read_timeline():
    """codex P0：write 仅入库，必须 consolidate(cognify) 后才能 read 出图谱。"""
    a = _adapter()
    a.write("u1", _turn())
    # 未 consolidate → 图谱空
    rr0 = a.read("u1", "团子吃什么", ReadOpts(top_k=5))
    assert rr0.memories == []
    # consolidate 后才有
    a.consolidate("u1")
    rr1 = a.read("u1", "团子吃什么", ReadOpts(top_k=5))
    assert rr1.memories
    assert rr1.memories[0].text_nature == "synthesized"


def test_consolidate_uses_zh_prompt():
    """中文铁律：cognify 必须带 ZH_PROMPT（默认英文 prompt 抽英文实体）。"""
    a = _adapter()
    a.write("u1", _turn())
    a.consolidate("u1")
    assert a._cognee.last_custom_prompt is not None
    assert "禁止" in a._cognee.last_custom_prompt and "中文" in a._cognee.last_custom_prompt


def test_neighborhood_depth_passed():
    a = _TestCogneeAdapter(cognee_module=_FakeCognee(), config={"neighborhood_depth": 3})
    a.write("u1", _turn())
    a.consolidate("u1")
    a.read("u1", "团子", ReadOpts(top_k=5))
    assert a._cognee.last_depth == 3


def test_content_hash_idempotent():
    a = _adapter()
    a.write("u1", _turn())
    s2 = a.write("u1", _turn())  # 同文本 → content hash 命中
    assert s2.raw.get("deduplicated") is True
    a.consolidate("u1")
    # 底层只 add 一次
    assert len(a._cognee._added) == 1


def test_traceback_wrapped_synthesized():
    """codex P0：GRAPH_COMPLETION 合成无源引用——溯源诚实标 wrapped。"""
    a = _adapter()
    a.write("u1", _turn())
    a.consolidate("u1")
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    m = rr.memories[0]
    assert m.source_turn_ids == []
    assert m.provenance_mode == "wrapped"
    by = {c.feature: c for c in a.get_capability_profile().capabilities}
    assert by["turn_id_traceback"].level == "wrapped"
    assert by["lineage_after_consolidate"].level == "declared"  # 虚拟时钟+溯源缺口诚实


def test_clear_global_prune():
    a = _adapter()
    a.write("u1", _turn())
    a.consolidate("u1")
    a.clear("u1")
    assert a._cognee._pruned_system is True
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    assert rr.memories == []


def test_empty_user_id_rejected():
    a = _adapter()
    with pytest.raises(AdapterError):
        a.write("", _turn())


def test_lane_c_incompatible():
    a = _adapter()
    prof = a.get_capability_profile()
    assert prof.lane_compatibility["C"] == "incompatible"
    by = {c.feature: c for c in prof.capabilities}
    assert by["write_semantic_sync"].level == "declared"  # write→consolidate→read 诚实
