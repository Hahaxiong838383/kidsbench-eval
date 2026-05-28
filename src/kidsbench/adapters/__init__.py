"""KidsBench Adapter 实现层。

三个基线（不依赖第三方）已就绪：
- NoMemoryAdapter   地板基线
- FullHistoryAdapter 对照基线
- OracleAdapter     天花板基线

第三方 Adapter（按需安装 optional-dependencies 后启用）：
- Mem0Adapter       (extras: mem0)
- LettaAdapter      (extras: letta)
- MemoryOSAdapter   待补
- HermesAdapter     自研系统，待对接
"""
from .fullhistory import FullHistoryAdapter
from .graphiti_adapter import GraphitiAdapter
from .mem0_adapter import Mem0Adapter
from .memoryos_adapter import MemoryOSAdapter
from .nomemory import NoMemoryAdapter
from .oracle import OracleAdapter

__all__ = [
    # 基线（不依赖第三方）
    "FullHistoryAdapter",
    "NoMemoryAdapter",
    "OracleAdapter",
    # Wave 1 第三方（按需 extras 安装）
    "Mem0Adapter",
    "MemoryOSAdapter",
    "GraphitiAdapter",
]
