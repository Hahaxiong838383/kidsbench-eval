"""噪声注入器测试（T7 可复现性）。"""
from __future__ import annotations

import pytest

from kidsbench.middleware import inject


def test_reproducible_same_seed():
    """同 seed → 完全相同（可复现，解题库担心）。"""
    a = inject("我最喜欢三角龙啦它头上有三只角", noise_type="homophone", intensity=0.5, seed=42)
    b = inject("我最喜欢三角龙啦它头上有三只角", noise_type="homophone", intensity=0.5, seed=42)
    assert a == b


def test_intensity_zero_unchanged():
    """intensity=0 → 原文不变。"""
    src = "我在家里做作业"
    assert inject(src, noise_type="homophone", intensity=0.0, seed=1) == src


def test_intensity_one_changes():
    """intensity=1 + 含可替换字 → 必有改动。"""
    src = "我在家再做作业"  # 含 在/再/做/作 等同音对
    out = inject(src, noise_type="homophone", intensity=1.0, seed=7)
    assert out != src


def test_filler_inserts():
    """filler 高强度 → 文本变长（插入填充词）。"""
    src = "我喜欢恐龙真的特别喜欢"
    out = inject(src, noise_type="filler", intensity=1.0, seed=3)
    assert len(out) > len(src)


def test_unknown_type_raises():
    with pytest.raises(ValueError, match="未知 noise_type"):
        inject("x", noise_type="bad_type", intensity=0.5, seed=1)


def test_invalid_intensity_raises():
    with pytest.raises(ValueError, match="intensity"):
        inject("x", noise_type="homophone", intensity=1.5, seed=1)


def test_empty_text():
    assert inject("", noise_type="homophone", intensity=0.5, seed=1) == ""


def test_does_not_mutate_input():
    """不可变：返回新串，不改入参。"""
    src = "我在家"
    _ = inject(src, noise_type="mixed", intensity=0.8, seed=1)
    assert src == "我在家"


def test_mixed_combines():
    """mixed 同 seed 可复现。"""
    a = inject("我在家做作业很开心", noise_type="mixed", intensity=0.6, seed=9)
    b = inject("我在家做作业很开心", noise_type="mixed", intensity=0.6, seed=9)
    assert a == b
