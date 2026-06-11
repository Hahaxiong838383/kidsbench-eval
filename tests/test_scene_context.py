"""scene_context（W3 模块A 当下感知）测试：只进 prompt + 向后兼容。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.run_eval import build_prompt, render_scene_context


def test_no_scene_backward_compat():
    """无 scene_context → system 与旧版逐字相同（27 题/14 题不破的保证）。"""
    s, u = build_prompt("我最爱的恐龙是哪个", [{"text": "喜欢三角龙"}])
    assert s == (
        "你是 K12 儿童 AI 陪伴助手。请结合「相关记忆」简短回答用户的问题。"
        "如果记忆里没有相关信息，请直接说不知道，不要编造。"
        "回答控制在 30 字以内。"
    )
    assert "当前场景" not in u


def test_with_scene_renders_into_prompt():
    sc = {"用户": "天天(10岁/四年级)", "时间": "周五19:20", "摄像头": "看向别处60s"}
    s, u = build_prompt("q", [{"text": "m"}], sc)
    assert "「当前场景」" in s
    assert "当前场景：用户=天天(10岁/四年级) ｜ 时间=周五19:20 ｜ 摄像头=看向别处60s" in u


def test_empty_scene_no_render():
    """空 dict → 不渲染，system 退回旧版。"""
    s, u = build_prompt("q", [], {})
    assert "「当前场景」" not in s and "当前场景" not in u


def test_render_skips_empty_values():
    """省略空维度（按需填）。"""
    out = render_scene_context({"麦克风": "嘈杂", "摄像头": ""})
    assert "麦克风=嘈杂" in out and "摄像头=" not in out


def test_render_none():
    assert render_scene_context(None) == ""


# ============= current_session（协议 v1.1，2026-06-11）=============


def test_no_current_session_backward_compatible():
    """缺省 current_session → system/user 与旧版逐字相同（旧 124 题零影响）。"""
    s_old, u_old = build_prompt("q", [{"text": "m"}])
    s_new, u_new = build_prompt("q", [{"text": "m"}], None, None)
    assert s_old == s_new and u_old == u_new
    assert "当前对话" not in u_new


def test_current_session_renders_into_prompt():
    cur = [
        {"turn_id": "c_001", "role": "assistant", "speaker": "小可",
         "text": "天天晚上好～", "timestamp": 1},
        {"turn_id": "c_002", "role": "user", "speaker": "天天",
         "text": "今天被班主任当着全班说了，烦死了", "timestamp": 2},
    ]
    s, u = build_prompt("[系统事件] 坐姿=趴下", [{"text": "m"}], None, cur)
    # 事件触发 → 主动陪伴模式 system（2026-06-11 smoke 教训：问答式 prompt
    # 下连 Oracle 都不使用记忆）
    assert "小可" in s and "必须自然地融入回应" in s
    assert "当前对话（本次会话刚刚发生）：" in u
    assert "小可: 天天晚上好～" in u
    assert "孩子: 今天被班主任当着全班说了，烦死了" in u
    # 段落顺序：记忆在前、当前对话在后、触发输入最后
    assert u.index("相关记忆") < u.index("当前对话") < u.index("当前事件")


def test_current_session_system_role():
    cur = [{"turn_id": "c_001", "role": "system", "speaker": "system",
            "text": "[系统已通知监护人]", "timestamp": 1}]
    _, u = build_prompt("q", [], None, cur)
    assert "系统: [系统已通知监护人]" in u


def test_empty_current_session_no_render():
    s, u = build_prompt("q", [], None, [])
    assert "当前对话" not in u and "「当前对话」" not in s
