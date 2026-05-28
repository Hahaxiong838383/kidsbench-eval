"""MemoryAdapter ABC：所有适配器必须实现的 7 个方法。

设计原则：
1. **不可变**：Adapter 不持有可变状态，状态全部在外部存储（vector db / graph db / API）
2. **fail-fast**：任何方法失败必须抛 AdapterError，禁止静默吞错
3. **真实性**：返回数据必须真实，硬编码占位符（如 return ["t_001"]）禁止
4. **能力诚实**：通过 get_capability_profile() 显式声明补齐策略
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .capability import CapabilityProfile
from .types import (
    ClearStats,
    ConsolidateStats,
    Dependency,
    FlushStats,
    ReadOpts,
    ReadResult,
    Turn,
    WriteStats,
)


class AdapterError(Exception):
    """所有 Adapter 内部错误统一抛这个。"""


# 范式标签：representation × retrieval × write_policy × controller × cognitive
ParadigmTags = dict[str, str | list[str]]


class MemoryAdapter(ABC):
    """记忆系统适配器抽象基类。

    实现时：
    - 子类 __init__ 接收配置（API key / endpoint / 等），不接收外部状态
    - 所有方法接收 user_id 作为隔离键
    - 异常一律 raise AdapterError，禁止 try/except 后静默返回空
    """

    # ---- 元数据 ----

    name: str = ""
    """Adapter 标识，例：'mem0' / 'letta' / 'oracle'。"""

    paradigm_tags: ParadigmTags = {}
    """四轴 + cognitive 范式标签。子类必须覆写。

    例：{
        "representation": "vector+entity",
        "retrieval": "rerank",
        "write_policy": "reactive",
        "controller": "rule",
        "cognitive": ["semantic"],
    }
    """

    # ---- 七个方法 ----

    @abstractmethod
    def write(self, user_id: str, turn: Turn) -> WriteStats:
        """灌入一轮对话（语义同步）。

        语义同步要求：
        - 返回时该 turn 的基本提取必须完成（写完立即 read 必须能查到，否则评测会误判"写入失败"）
        - 异步索引构建（如 KG 合并）可放后台，但 sidecar 必须同步建好

        Adapter 必须以某种方式保留 turn.turn_id 的可追溯性（natively 或 wrapped 或 sidecar）。
        """

    def batch_write(self, user_id: str, turns: list[Turn]) -> list[WriteStats]:
        """批量灌入（默认实现 = 循环 write，子类可覆写实现真正的批量优化）。

        评测初始化时灌入历史会一次性调几十~几百 turns，子类有 batch_add 等批量 API 时
        强烈建议覆写本方法以减少 LLM/Embedding API 调用次数。
        """
        return [self.write(user_id, t) for t in turns]

    @abstractmethod
    def read(self, user_id: str, query: str, opts: ReadOpts | None = None) -> ReadResult:
        """根据 query 召回相关记忆。

        返回的 Memory 必须填 source_turn_ids（主路）或 source_embedding（辅路）。
        """

    @abstractmethod
    def clear(self, user_id: str) -> ClearStats:
        """物理清场：彻底删除 user_id 的所有记忆 / 向量 / 图关系。

        - 必须真删，不能软删
        - 必须同步等待删除完成，不能异步返回
        - 删除后立即 read 必须返回空列表（验证锚点）
        """

    @abstractmethod
    def flush(self, user_id: str) -> FlushStats:
        """强制把 write 缓冲全部落盘 + 索引就绪（轻量，无 LLM 调用）。

        在 write 批量灌完 → read 之前必须调，防止异步索引未到位导致召回空。
        和 consolidate 的区别：flush 只等索引就绪（毫秒级），不做 LLM 语义整理。
        """

    def consolidate(self, user_id: str) -> ConsolidateStats:
        """语义固化：调 LLM 做 short→mid→long 整理 / KG 合并 / 实体消歧（秒级，吃 token）。

        默认实现 = no-op（返回 success=True, latency=0），适用于不做语义固化的 adapter
        （NoMemory / Mem0 等已经在 write 时同步做完的）。

        MemoryOS / Graphiti / Cognee 这种有显式 consolidate pipeline 的必须覆写。

        评测协议：Harness 在 batch_write 后 → flush → consolidate → read，三步必须分别可触发，
        因为 consolidate 耗时 10-30 秒，不能放在 write/flush 里硬等。
        """
        return ConsolidateStats(success=True, latency_ms=0.0, consolidated_count=0)

    @abstractmethod
    def get_dependencies(self) -> list[Dependency]:
        """启动前 preflight 检查清单。

        例：[Dependency('qdrant', 'service', True, 'curl http://localhost:6333')]
        """

    @abstractmethod
    def get_stats(self, user_id: str) -> dict[str, Any]:
        """返回 Adapter 内部计数 / 耗时统计。

        建议字段：{'total_writes': N, 'total_reads': N, 'avg_write_ms': N, ...}
        """

    @abstractmethod
    def get_capability_profile(self) -> CapabilityProfile:
        """声明本 Adapter 对标准能力的支持情况 + 补齐策略。"""

    # ---- 工具方法（可选覆写） ----

    def health_check(self) -> bool:
        """快速自检，默认调一遍 get_dependencies 看是否都通过。

        子类可覆写实现更精细的检查。
        """
        try:
            deps = self.get_dependencies()
            return all(not d.required or self._check_dep(d) for d in deps)
        except Exception:
            return False

    def _check_dep(self, dep: Dependency) -> bool:
        """默认实现：直接返回 True，子类按需实现具体检查。"""
        return True

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.name})>"
