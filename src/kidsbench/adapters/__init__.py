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
from .nomemory import NoMemoryAdapter
from .oracle import OracleAdapter

__all__ = [
    "NoMemoryAdapter",
    "FullHistoryAdapter",
    "OracleAdapter",
]
