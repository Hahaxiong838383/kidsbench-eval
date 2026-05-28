"""Capability Profile：Adapter 自报能力，作为补齐策略的锚点。

Why：候选系统原生缺失某些方法时（如 Mem0 不原生保留 turn_id），Adapter 必须显式
声明用了什么补齐策略（real / wrap / simulate / declare），避免静默作弊。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CapabilityLevel = Literal[
    "native",      # 候选系统原生支持
    "wrapped",     # Adapter 用真实数据包装（如 metadata.turn_id）
    "computed",    # Adapter 用真数据计算（如 cosine 反查 turn_id）
    "simulated",   # Adapter 用经验值估算（如 cost_token 估算，1-3% 误差）
    "declared",    # Adapter 声明不支持，返回明确空值（不是 hardcode 占位）
    "unsupported", # 候选系统物理上无法实现
]


@dataclass(frozen=True)
class Capability:
    """单个能力的声明。"""

    feature: str
    """能力名，例：'turn_id_traceback' / 'physical_clear' / 'cognitive_type_filter'。"""

    level: CapabilityLevel
    note: str = ""
    """实现细节说明，例：'metadata.turn_id 注入 + 取回'。"""


LaneCompat = Literal["compatible", "incompatible", "degraded"]
"""Lane 适配性：
- compatible:   可在该 Lane 下跑评测，结果可比
- incompatible: 该 Lane 跑不了（如内部锁 GPT-4 跑不了 Lane A1=锁 Qwen）
- degraded:     能跑但需要降级（如 fallback 兜底，结果带星号标记）
"""


@dataclass(frozen=True)
class CapabilityProfile:
    """Adapter 的完整能力声明 + Lane 适配性。"""

    adapter_name: str
    capabilities: list[Capability] = field(default_factory=list)
    lane_compatibility: dict[str, LaneCompat] = field(default_factory=dict)
    """例：{"A1": "compatible", "A2": "compatible", "B": "compatible"}
    或：{"A1": "incompatible", "A2": "compatible", "B": "compatible"}
    Lane 定义见 v3 评测协议（A1=锁 Qwen 内外层 / A2=锁 GPT-4 内外层 / B=自由内层）。
    """
    lane_notes: dict[str, str] = field(default_factory=dict)
    """每档 Lane 的备注：incompatible 原因 / degraded 降级方式。
    例：{"A1": "internal LLM 硬锁 GPT-4，无法换 Qwen3-Max"}
    """

    def get(self, feature: str) -> Capability | None:
        for c in self.capabilities:
            if c.feature == feature:
                return c
        return None

    def summary(self) -> dict[str, str]:
        """生成 {feature: level} 简表，给 capability_matrix.md 用。"""
        return {c.feature: c.level for c in self.capabilities}

    def supports_lane(self, lane: str) -> bool:
        """是否能跑该 Lane（compatible 或 degraded 都算能跑）。"""
        return self.lane_compatibility.get(lane, "incompatible") != "incompatible"


# 评测协议要求的标准能力清单（Harness 据此判断是否能跑 + 怎么补齐扣分）
STANDARD_FEATURES = [
    "physical_clear",          # clear 是否真物理删除（防幽灵记忆残留）
    "turn_id_traceback",       # 能否追溯召回记忆的 source_turn_ids
    "cognitive_type_filter",   # 能否按 Episodic/Semantic/Procedural 过滤召回
    "score_normalized",        # score 是否归一化到 [0,1]
    "concurrent_safe",         # 并发 user_id 是否真隔离
    "cost_accounting",         # 能否报告 token 消耗
    "embedding_export",        # 能否导出记忆文本的 embedding（辅路用）
    "flush_blocking",          # flush 是否真等待索引就绪（防异步未到位）
]
