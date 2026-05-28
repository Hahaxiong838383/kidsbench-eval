"""KidsBench L1 契约层。

任何新 Adapter 只需 import 本模块即可拿到所有需要的抽象基类 + 数据类。
"""
from .adapter import AdapterError, MemoryAdapter, ParadigmTags
from .capability import (
    STANDARD_FEATURES,
    Capability,
    CapabilityLevel,
    CapabilityProfile,
    LaneCompat,
)
from .types import (
    ClearStats,
    Dependency,
    FlushStats,
    Memory,
    ReadOpts,
    ReadResult,
    Role,
    Turn,
    WriteStats,
)

__all__ = [
    # adapter
    "MemoryAdapter",
    "AdapterError",
    "ParadigmTags",
    # capability
    "Capability",
    "CapabilityLevel",
    "CapabilityProfile",
    "LaneCompat",
    "STANDARD_FEATURES",
    # types
    "Turn",
    "Memory",
    "Role",
    "WriteStats",
    "ReadResult",
    "ReadOpts",
    "ClearStats",
    "FlushStats",
    "Dependency",
]
