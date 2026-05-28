"""核心数据类型：Turn / Memory / Stats 等不可变 dataclass。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]


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


@dataclass(frozen=True)
class ReadOpts:
    """read 的可选参数（开放给 Harness 调）。"""

    top_k: int = 5
    score_threshold: float = 0.0
    cognitive_filter: list[str] | None = None
    """限定召回类型：['episodic'] / ['semantic'] / None=不限。"""

    extra: dict[str, Any] = field(default_factory=dict)
