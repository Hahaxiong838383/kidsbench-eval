"""Hindsight adapter 专属测试（评审钉死的必测项）。

覆盖：幂等查重（gpt-5.5 重试重复）/ 双模式成本差异（范式旋钮）/
bank 物理隔离（gemini 反作弊）/ recall 模式 consolidate 禁 LLM（成本归属）/
typed 错误不吞成空（fail-fast）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from kidsbench.adapters import HindsightAdapter
from kidsbench.contract import AdapterError, ReadOpts, Turn
from kidsbench.middleware import NetworkError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_contract import _HindsightContractClient


def _turn(tid: str = "t_001", text: str = "我家的布偶猫叫团子") -> Turn:
    return Turn(turn_id=tid, session_id="s1", role="user", text=text, timestamp=time.time())


def test_write_idempotent_dedup():
    """同 turn 写两次 → 第二次去重，底层只 retain 一次（防 retry 重复记忆）。"""
    client = _HindsightContractClient()
    adapter = HindsightAdapter(mode="recall", client=client)
    adapter.write("u1", _turn())
    s2 = adapter.write("u1", _turn())
    assert s2.raw.get("deduplicated") is True
    assert client._counter == 1  # 底层只写了一次
    rr = adapter.read("u1", "团子", ReadOpts(top_k=5))
    assert len(rr.memories) == 1  # 召回只有一份


def test_dual_mode_cost_divergence():
    """范式旋钮核心断言：recall read 成本=0，reflect read 成本>0。"""
    recall_ad = HindsightAdapter(mode="recall", client=_HindsightContractClient())
    reflect_ad = HindsightAdapter(mode="reflect", client=_HindsightContractClient())
    recall_ad.write("u1", _turn())
    reflect_ad.write("u1", _turn())
    rr_recall = recall_ad.read("u1", "团子", ReadOpts(top_k=5))
    rr_reflect = reflect_ad.read("u1", "团子", ReadOpts(top_k=5))
    assert rr_recall.cost_token == 0
    assert rr_reflect.cost_token > 0


def test_bank_isolation_between_modes():
    """反作弊：双模式共享同一底层 client 时，recall 身份读不到 reflect 身份的数据。"""
    shared = _HindsightContractClient()
    recall_ad = HindsightAdapter(mode="recall", client=shared)
    reflect_ad = HindsightAdapter(mode="reflect", client=shared)
    reflect_ad.write("u1", _turn(text="reflect 身份独有的记忆"))
    rr = recall_ad.read("u1", "记忆", ReadOpts(top_k=5))
    assert rr.memories == []  # bank 后缀隔离，互不可见


def test_recall_mode_consolidate_no_llm():
    """recall 身份 consolidate 必须零成本（防「廉价检索点」人设崩塌）。"""
    client = _HindsightContractClient()
    adapter = HindsightAdapter(mode="recall", client=client)
    adapter.write("u1", _turn())
    cs = adapter.consolidate("u1")
    assert cs.success and cs.cost_token == 0 and cs.consolidated_count == 0


def test_reflect_synthesis_is_first_memory():
    """晚绑定核心产出：synthesis 作为首条 Memory，标 synthesized。"""
    adapter = HindsightAdapter(mode="reflect", client=_HindsightContractClient())
    adapter.write("u1", _turn())
    rr = adapter.read("u1", "团子", ReadOpts(top_k=5))
    assert rr.memories, "reflect 应返回 synthesis + facts"
    first = rr.memories[0]
    assert first.text_nature == "synthesized"
    assert first.metadata.get("kind") == "synthesis"


def test_backend_error_raises_typed_not_empty():
    """fail-fast：后端故障必须抛 typed exception，不许吞成空召回。"""

    class _DownClient(_HindsightContractClient):
        def recall(self, bank_id, query, **kw):
            import httpx

            raise httpx.ConnectError("server down")

    adapter = HindsightAdapter(mode="recall", client=_DownClient())
    with pytest.raises(NetworkError):
        adapter.read("u1", "团子")


def test_invalid_mode_raises():
    with pytest.raises(AdapterError):
        HindsightAdapter(mode="hybrid", client=_HindsightContractClient())


def test_clear_physical_and_sidecar():
    client = _HindsightContractClient()
    adapter = HindsightAdapter(mode="recall", client=client)
    adapter.write("u1", _turn())
    adapter.clear("u1")
    assert adapter.read("u1", "团子").memories == []
    # clear 后同 turn 可重写（sidecar 哨兵已清，幂等不误杀）
    s = adapter.write("u1", _turn())
    assert not s.raw.get("deduplicated")


def test_capability_differs_by_mode():
    """capability 必须按 mode 如实分别申报（评审收敛：随 mode 区分 reporting）。"""
    recall_prof = HindsightAdapter(mode="recall", client=_HindsightContractClient()).get_capability_profile()
    reflect_prof = HindsightAdapter(mode="reflect", client=_HindsightContractClient()).get_capability_profile()
    assert recall_prof.get("turn_id_traceback").level == "wrapped"
    assert reflect_prof.get("turn_id_traceback").level == "computed"
    assert recall_prof.supports_lane("C") and not reflect_prof.supports_lane("C")
