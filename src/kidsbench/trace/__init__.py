"""KidsBench Trace 模块（B1）。

把 harness 跑题的每一步白盒化：用 OTel-lite span 模型记录嵌套调用，
经双通道 exporter（实时 HTTP POST + 本地 jsonl 兜底）持久化。

零侵入设计：
- @span 装饰器可选；不 init trace 时，所有 span 静默无开销
- exporter 失败绝不抛错到业务流程
- run_id 通过 contextvars 自动传递，不污染函数签名

用法：
    from kidsbench.trace import span, init_run, finalize_run

    @span("adapter.write")
    def write(self, user_id, turn):
        ...

    # harness 入口：
    with init_run(run_id="r_001", qid="q_001"):
        adapter.write(...)
        adapter.read(...)
"""

from .adapter_wrap import TracedAdapter, wrap
from .exporter import (
    Exporter,
    HttpExporter,
    JsonlExporter,
    MultiExporter,
    NullExporter,
    set_exporter,
)
from .span import (
    SpanEvent,
    finalize_run,
    init_run,
    is_tracing,
    span,
    span_attr,
)

__all__ = [
    "SpanEvent",
    "span",
    "span_attr",
    "init_run",
    "finalize_run",
    "is_tracing",
    "TracedAdapter",
    "wrap",
    "Exporter",
    "HttpExporter",
    "JsonlExporter",
    "MultiExporter",
    "NullExporter",
    "set_exporter",
]
