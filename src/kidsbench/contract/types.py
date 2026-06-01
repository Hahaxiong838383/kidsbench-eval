"""核心数据类型：Turn / Memory / Stats 等不可变 dataclass。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]

# Schema 版本（frozen dataclass 演进追踪，长期多轮 runs 反序列化对比，grok #7）
__schema_version__ = "2.0"

# 可观测诊断字段类型（判分不依赖自报，仅白盒展示，默认 unknown 向后兼容）
ProvenanceMode = Literal["native", "wrapped", "computed", "fallback", "unknown"]
TextNature = Literal["verbatim", "extracted", "synthesized", "unknown"]
ConsolidationPhase = Literal["write_time", "explicit", "unknown"]


@dataclass(frozen=True)
class Turn:
    """一轮对话（题库中的最小灌入单位）。"""

    turn_id: str
    """全局唯一 id，例 't_001'。Adapter 必须以某种方式保留映射回这个 id 的能力。"""

    session_id: str
    """会话 id，跨会话一致性评测的边界。"""

    role: Role
    text: str
    timestamp: float
    """Unix 秒，浮点。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """自由扩展字段：cognitive_type / topic / emotion_label 等。"""


@dataclass(frozen=True)
class Memory:
    """一条召回的记忆。"""

    memory_id: str
    """Adapter 内部 id（可能是 vector id / entity id / fact id，任意）。"""

    text: str
    score: float
    """召回相关性分数，归一化到 [0, 1]。"""

    source_turn_ids: list[str] = field(default_factory=list)
    """主路：声明该记忆来自哪些原始 turn_id。
    若 Adapter 内部无法追踪，可走辅路（source_embedding）做 cosine 反向匹配。
    禁止硬编码占位符（防"看似通"陷阱）。
    """

    source_embedding: list[float] | None = None
    """辅路：记忆文本的向量表示，Harness 用 cosine > 0.85 反查 gold_turn。"""

    timestamp: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # 可观测诊断（C/B 决策：判分不依赖这些自报字段，仅白盒展示/归因）
    provenance_mode: ProvenanceMode = "unknown"
    """native=原生精确1:1 / wrapped=sidecar注入 / computed=cosine辅路反查 / fallback=全量兜底 / unknown=升级前。"""
    text_nature: TextNature = "unknown"
    """verbatim=原文片段 / extracted=LLM抽取fact / synthesized=固化改写 / unknown=升级前。"""


@dataclass(frozen=True)
class WriteStats:
    """write 调用的统计信息。"""

    success: bool = True
    latency_ms: float = 0.0
    cost_token: int = 0
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadResult:
    """read 调用的返回。"""

    memories: list[Memory]
    latency_ms: float = 0.0
    cost_token: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClearStats:
    success: bool = True
    latency_ms: float = 0.0
    deleted_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class FlushStats:
    success: bool = True
    latency_ms: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class ConsolidateStats:
    """consolidate（语义固化）的统计。

    跟 flush（落盘）的区别：
    - flush:       同步索引就绪，~毫秒，无 LLM 调用
    - consolidate: 调 LLM 做 short→mid→long 语义整理 / KG 合并 / 实体消歧，~秒级
    """

    success: bool = True
    latency_ms: float = 0.0
    cost_token: int = 0
    """consolidate 通常吃大量 token，必须报告。"""

    consolidated_count: int = 0
    """本次固化合并/抽取的记忆数。"""

    consolidation_phase: ConsolidationPhase = "unknown"
    """grok：write_time=write时已固化(mem0) / explicit=独立consolidate(memoryos/graphiti) / unknown。解决 mem0 no-op 与他家 metrics 不可比。"""

    error: str | None = None


@dataclass(frozen=True)
class Dependency:
    """启动前 preflight 自检 + Lane 适配性判定的依赖描述。

    kind 语义：
    - service:        外部服务（docker container / 远端 API host）
    - api:            云端 API（OpenAI / dashscope 等）
    - model:          模型权重文件（本地加载）
    - env:            环境变量
    - internal_llm:   Adapter 内部调用的 LLM（Lane 判定关键）
    - internal_embed: Adapter 内部用的 embedding（Lane 判定关键）
    """

    name: str
    """例：'qdrant' / 'gpt-4' / 'bge-m3'。"""

    kind: Literal["service", "api", "model", "env", "internal_llm", "internal_embed"]
    required: bool = True
    check_hint: str = ""
    """例：'curl http://localhost:6333/healthz' / 'env MEM0_API_KEY'。"""

    swap_supported: bool = True
    """该依赖是否可被替换。

    对 internal_llm / internal_embed 尤其关键 —— 决定本 adapter 能否跑 Lane A1
    （锁定 Qwen3-Max 内外层）。例：
    - Letta 的内部 LLM 默认 OpenAI，但接受用户传入 → swap_supported=True
    - Mem0 的 embedding 默认 OpenAI ada-002，可改 bge-m3 → swap_supported=True
    - 某 adapter 硬编码 GPT-4 拒绝替换 → swap_supported=False
    """

    config_key: str = ""
    """注入路径声明（grok A 决策）。例 'llm.model' / 'llm_model'。preflight 校验注入是否真生效用。"""
    actual_model: str | None = None
    """构造时探测到的真实注入 model；None=未探测。供 Lane 锁定运行时断言。"""


@dataclass(frozen=True)
class ReadOpts:
    """read 的可选参数（开放给 Harness 调）。"""

    top_k: int = 5
    score_threshold: float = 0.0
    cognitive_filter: list[str] | None = None
    """限定召回类型：['episodic'] / ['semantic'] / None=不限。"""

    include_provenance: bool = True
    """是否强制返回 source_turn_ids（评测验证默认 True；性能场景可设 False 减少图回溯）。"""

    current_timestamp: float | None = None
    """E 决策：query 发生时刻，透传给支持时间的系统（T3 矛盾更新）。adapter 不支持则静默忽略，不 raise。"""

    extra: dict[str, Any] = field(default_factory=dict)
