"""实时 / 准实时状态快照（B0 修订版）。

B0 取舍：
- graphiti: **优雅降级**——优先实时连 FalkorDB；失败时返回 fallback（最近 run snapshot）+ warning
  *不再返 503*，前端可正常渲染，看 warning 知道是不是 tunnel 没开
- mem0 / memoryos: 从最近一次匹配 adapter 的 results.jsonl 行抽 stats（永远非实时）

Gemini D.3 / A.1 合并：endpoint 永远 200，错误信息塞 body 让前端展示。
"""

from typing import Any

from fastapi import APIRouter

from .config import FALKOR_HOST, FALKOR_PORT, RUNS_PATH
from .runs import _iter_groups, _iter_results_jsonl

router = APIRouter(prefix="/api/state", tags=["state"])


def _latest_for_adapter(adapter_name: str) -> dict | None:
    """扫所有 groups 找最近一次 (adapter == adapter_name) 的 results.jsonl 行 + group summary。"""
    if not RUNS_PATH.exists():
        return None

    latest_row: dict | None = None
    latest_ts: float = 0.0
    latest_group: str = ""
    latest_summary: dict | None = None

    for name, group_dir, summary in _iter_groups():
        for row in _iter_results_jsonl(group_dir / "results.jsonl"):
            if row.get("adapter") != adapter_name:
                continue
            ts = row.get("timestamp", 0.0)
            if ts > latest_ts:
                latest_ts = ts
                latest_row = row
                latest_group = name
                latest_summary = summary.get(adapter_name) if isinstance(summary, dict) else None
    if latest_row is None:
        return None
    return {
        "group": latest_group,
        "summary_in_group": latest_summary,
        "latest_row": latest_row,
    }


def _adapter_stats(adapter_name: str) -> dict:
    snapshot = _latest_for_adapter(adapter_name)
    return {
        "adapter": adapter_name,
        "real_time": False,
        "source": "latest matching row in runs/<group>/results.jsonl",
        "snapshot": snapshot,
    }


@router.get("/mem0")
def state_mem0() -> dict:
    return _adapter_stats("mem0")


@router.get("/memoryos")
def state_memoryos() -> dict:
    return _adapter_stats("memoryos")


@router.get("/graphiti")
def state_graphiti() -> dict:
    """优雅降级：先试实时 FalkorDB，失败回退到 results.jsonl + warning。

    永远返回 200，不返 503。前端按 warning 字段判断是否要提示用户开 tunnel。
    """
    fallback = _adapter_stats("graphiti")
    base: dict[str, Any] = {
        **fallback,
        "source": f"FalkorDB {FALKOR_HOST}:{FALKOR_PORT} (try real-time)",
        "graphs": None,
        "graphs_count": None,
        "latest_run": fallback.get("snapshot"),
    }

    try:
        from falkordb import FalkorDB
    except ImportError as exc:
        return {
            **base,
            "real_time": False,
            "warning": f"FalkorDB SDK 未装：{exc!s}",
        }

    try:
        db = FalkorDB(host=FALKOR_HOST, port=FALKOR_PORT, socket_timeout=2)
        graph_names = db.list_graphs()
    except Exception as exc:
        return {
            **base,
            "real_time": False,
            "warning": (
                f"FalkorDB 不可达（{FALKOR_HOST}:{FALKOR_PORT}：{exc!s}）。"
                f"显示的是最近一次 run 的历史快照。"
                f"开启实时拉取：在终端跑 ssh -L 16379:127.0.0.1:16379 prnas -N"
            ),
        }

    # 实时拉成功
    graphs_detail = []
    for name in graph_names:
        try:
            g = db.select_graph(name)
            node_count = g.query("MATCH (n) RETURN COUNT(n) AS c").result_set[0][0]
            edge_count = g.query("MATCH ()-[r]->() RETURN COUNT(r) AS c").result_set[0][0]
            graphs_detail.append(
                {"name": name, "nodes": node_count, "edges": edge_count, "ok": True}
            )
        except Exception as exc:
            graphs_detail.append({"name": name, "ok": False, "error": str(exc)})

    return {
        "adapter": "graphiti",
        "real_time": True,
        "source": f"FalkorDB {FALKOR_HOST}:{FALKOR_PORT} (live)",
        "graphs": graphs_detail,
        "graphs_count": len(graph_names),
        "snapshot": fallback.get("snapshot"),
        "latest_run": fallback.get("snapshot"),
    }
