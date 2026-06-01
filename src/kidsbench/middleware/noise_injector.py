"""ASR 噪声注入器（T7 脏数据鲁棒性题，可复现）。

题库给干净原文 + noise_params{type, intensity, seed}，harness 灌入前注入脏文本。
seed 固定 → 完全可复现（解题库"手写脏文本不可复现、分布随意"的担心）。

探索期版：内置常见 K12 场景同音/形近/口语词表。词表可扩，分布精度后续迭代。

用法：
    from kidsbench.middleware import inject
    dirty = inject("我最喜欢三角龙", noise_type="homophone", intensity=0.15, seed=42)
"""
from __future__ import annotations

import random

# 常见中文同音字混淆（K12 口语/ASR 场景，可扩）
_HOMOPHONE: dict[str, str] = {
    "在": "再", "再": "在", "得": "的", "做": "作", "作": "做",
    "那": "哪", "事": "是", "图": "涂", "龙": "笼", "角": "脚",
    "洞": "动", "黑": "嘿", "象": "像", "像": "象", "及": "急",
    "带": "戴", "戴": "带", "坐": "座", "座": "坐", "买": "卖",
    "题": "提", "提": "题", "他": "它", "竟": "净",
}
# 形近字（ASR / OCR 误识）
_VISUAL: dict[str, str] = {
    "未": "末", "己": "已", "已": "己", "日": "曰", "土": "士",
    "大": "太", "太": "大", "末": "未", "拨": "拔", "拔": "拨",
}
# 口语填充词（语气/废话）
_FILLERS: list[str] = ["嗯", "那个", "就是", "然后", "这个", "呃", "啊"]

_VALID_TYPES = frozenset({"homophone", "asr_error", "filler", "mixed"})


def inject(clean_text: str, *, noise_type: str, intensity: float, seed: int) -> str:
    """对干净文本注入指定类型噪声。可复现（同 seed 同结果）。

    - noise_type: homophone(同音字) / asr_error(同音+形近) / filler(口语废话) / mixed
    - intensity: 0.0~1.0，字符级注入比例
    - seed: 固定随机种子 → 可复现

    返回新字符串，不修改入参。
    """
    if not clean_text:
        return clean_text
    if not 0.0 <= intensity <= 1.0:
        raise ValueError(f"intensity 必须 ∈ [0,1]，当前 {intensity}")
    if noise_type not in _VALID_TYPES:
        raise ValueError(f"未知 noise_type '{noise_type}'，合法：{sorted(_VALID_TYPES)}")

    rng = random.Random(seed)
    if noise_type == "homophone":
        return _substitute(clean_text, _HOMOPHONE, intensity, rng)
    if noise_type == "asr_error":
        return _substitute(clean_text, {**_HOMOPHONE, **_VISUAL}, intensity, rng)
    if noise_type == "filler":
        return _insert_fillers(clean_text, intensity, rng)
    # mixed：先同音替换（70% 权重）再插入填充词（50% 权重）
    substituted = _substitute(clean_text, _HOMOPHONE, intensity * 0.7, rng)
    return _insert_fillers(substituted, intensity * 0.5, rng)


def _substitute(text: str, table: dict[str, str], intensity: float, rng: random.Random) -> str:
    """按 intensity 概率把命中字替换成混淆字。返回新串。"""
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in table and rng.random() < intensity:
            chars[i] = table[ch]
    return "".join(chars)


def _insert_fillers(text: str, intensity: float, rng: random.Random) -> str:
    """在字符间按概率插入口语填充词。返回新串。"""
    out: list[str] = []
    for ch in text:
        out.append(ch)
        # 密度系数 0.3：避免高 intensity 时填充词爆炸
        if rng.random() < intensity * 0.3:
            out.append(rng.choice(_FILLERS))
    return "".join(out)
