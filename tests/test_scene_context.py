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
