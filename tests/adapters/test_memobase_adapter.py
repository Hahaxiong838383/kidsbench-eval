"""Memobase adapter 契约测试（不需起真 server）。

覆盖（Phase 0 + codex 对抗审钉死）：
- 画像中心 read：profile 条目包装成 Memory（extracted，非 verbatim）
- 溯源诚实：date-level wrapped（source_turn_ids 空 + provenance_mode=wrapped）
- flush(sync=True) 后画像才可读（write_semantic_sync=wrapped 语义）
- 物理清场 delete_user + 幂等查重
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from kidsbench.adapters import MemobaseAdapter
from kidsbench.contract import AdapterError, ReadOpts, Turn


class _FakeProfile:
    def __init__(self, topic, sub_topic, content, pid):
        self.topic = topic
        self.sub_topic = sub_topic
        self.content = content
        self.id = pid


class _FakeUser:
    def __init__(self):
        self._blobs: list = []
        self._flushed = False
        self._profiles: list = []

    def insert(self, blob):
        self._blobs.append(blob)
        self._flushed = False  # 新写入使画像失效，须重 flush
        return f"blob_{len(self._blobs)}"

    def flush(self, sync=False):
        # 模拟 LLM 抽取：把含「团子」的对话抽成画像
        self._flushed = True
        if any("团子" in str(getattr(b, "messages", b)) for b in self._blobs):
            self._profiles = [_FakeProfile("兴趣爱好", "宠物",
                                           "养了一只布偶猫，名叫团子。[提及于2026-06-05]", "p1")]
        return True

    def profile(self, max_token_size=1000, chats=None, **kw):
        if not self._flushed:
            return []  # 未 flush 画像为空（write_semantic_sync=wrapped）
        return list(self._profiles)


class _FakeMemobaseClient:
    def __init__(self):
        self._users: dict[str, _FakeUser] = {}
        self.deleted: list[str] = []

    def add_user(self, data):
        uid = f"u_{len(self._users) + 1}"
        self._users[uid] = _FakeUser()
        return uid

    def get_user(self, uid):
        return self._users[uid]

    def delete_user(self, uid):
        self.deleted.append(uid)
        self._users.pop(uid, None)
        return True


def _turn(tid="t_001", text="我家的布偶猫叫团子", ts=None):
    return Turn(turn_id=tid, session_id="s1", role="user", text=text,
                timestamp=ts if ts is not None else time.time())


class _TestMemobaseAdapter(MemobaseAdapter):
    @staticmethod
    def _make_chat_blob(messages):
        # 不依赖真 memobase 包：用轻量对象，fake user 按 .messages 读
        import types

        return types.SimpleNamespace(messages=messages)


def _adapter():
    return _TestMemobaseAdapter(client=_FakeMemobaseClient())


def test_profile_read_is_extracted_not_verbatim():
    a = _adapter()
    a.write("u1", _turn())
    a.flush("u1")
    rr = a.read("u1", "我的猫叫什么", ReadOpts(top_k=5))
    assert rr.memories, "flush 后应有画像"
    m = rr.memories[0]
    assert "团子" in m.text
    assert m.text_nature == "extracted"  # 画像是抽取，非原文
    assert m.metadata.get("kind") == "profile"


def test_traceback_is_date_level_wrapped():
    """codex P0：画像派生物溯源诚实——source_turn_ids 空 + wrapped。"""
    a = _adapter()
    a.write("u1", _turn())
    a.flush("u1")
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    m = rr.memories[0]
    assert m.source_turn_ids == []  # 不假装能绑定 turn
    assert m.provenance_mode == "wrapped"
    # 能力矩阵也如实标
    by = {c.feature: c for c in a.get_capability_profile().capabilities}
    assert by["turn_id_traceback"].level == "wrapped"


def test_read_before_flush_empty():
    """write 后未 flush → 画像未就位（write_semantic_sync=wrapped）。"""
    a = _adapter()
    a.write("u1", _turn())
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    assert rr.memories == []  # 必须 flush(sync) 后才可读


def test_idempotent_dedup():
    a = _adapter()
    a.write("u1", _turn())
    s2 = a.write("u1", _turn())
    assert s2.raw.get("deduplicated") is True


def test_physical_clear():
    a = _adapter()
    a.write("u1", _turn())
    a.flush("u1")
    cs = a.clear("u1")
    assert cs.success
    # 清场后无该 user，read 返回空
    rr = a.read("u1", "团子", ReadOpts(top_k=5))
    assert rr.memories == []


def test_empty_user_id_rejected():
    a = _adapter()
    with pytest.raises(AdapterError):
        a.write("", _turn())


def test_lane_c_incompatible():
    """画像抽取强依赖 LLM，C lane（无 LLM）不兼容——诚实声明。"""
    a = _adapter()
    prof = a.get_capability_profile()
    assert prof.lane_compatibility["C"] == "incompatible"


def test_system_role_clamped_to_assistant():
    """题库 system 旁白 turn 映射 assistant（ChatBlob Literal 校验，w3 smoke 回归）。"""
    a = _adapter()
    t = _turn(tid="t_sys", text="【场景】晚自习开始")
    object.__setattr__(t, "role", "system") if hasattr(t, "__dataclass_fields__") else None
    import dataclasses

    t2 = dataclasses.replace(t, role="system") if dataclasses.is_dataclass(t) else t
    a.write("u1", t2)
    user = a._ensure_client().get_user(a._uids["u1"])
    blob = user._blobs[-1]
    assert blob.messages[0]["role"] == "assistant"
