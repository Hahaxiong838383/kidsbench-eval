"""历史 run 列表（B0，适配真实 schema）。

真实目录结构（2026-05-29 实测）：

    runs/<group>/
    ├── results.jsonl    # 一行一 (qid, adapter) 评测结果
    └── summary.json     # 按 adapter 聚合的 correct/wrong/evasive/error/total

字段说明（results.jsonl 每行）：
    qid, adapter, user_id, success, error,
    recalled_count, recalled_turn_ids, recalled_texts,
    recall_metric: {recall, precision, hit_count, missed, extra},
    answer, llm_latency_ms, llm_tokens_in, llm_tokens_out,
    judge_score, judge_verdict (correct/wrong/evasive/error),
    judge_positive_hits, judge_negative_hits,
    timestamp, write_latency_ms, read_latency_ms

字段说明（summary.json）：
    {adapter_name: {correct, wrong, evasive, error, total}, ...}

group 命名约定：
    - mem0_bge / memoryos_bge / graphiti_bge        ← bge 切换后（after_bge）
    - with_mem0 / memoryos_only / with_graphiti_final ← bge 切换前（before_bge）
    - baseline_only / baseline_v2                    ← 仅基线
"""

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .config import RUNS_PATH

router = APIRouter(prefix="/api/runs", tags=["runs"])


# group 名 → 目标 adapter 标签（前端筛选用）
_GROUP_ADAPTER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"mem0", re.I), "mem0"),
    (re.compile(r"memoryos", re.I), "memoryos"),
    (re.compile(r"graphiti", re.I), "graphiti"),
    (re.compile(r"^baseline", re.I), "baseline"),
]


def _classify_target_adapter(group_name: str) -> str:
    for pattern, label in _GROUP_ADAPTER_PATTERNS:
        if pattern.search(group_name):
            return label
    return "unknown"


def _classify_era(group_name: str) -> str:
    if "_bge" in group_name:
        return "after_bge"
    if group_name in {
        "with_mem0",
        "memoryos_only",
        "with_graphiti",
        "with_graphiti_v2",
        "with_graphiti_v3",
        "with_graphiti_final",
        "graphiti_only",
        "graphiti_only2",
    }:
        return "before_bge"
    if group_name.startswith("baseline"):
        return "baseline"
    return "unknown"


def _safe_load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _iter_results_jsonl(path: Path):
    """逐行读 results.jsonl，跳过损坏行（Gemini A.3 容错读取）。"""
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # 末尾不完整行容忍
                    continue
    except OSError:
        return


def _iter_groups():
    """生成 (group_name, group_dir, summary_data) 元组。"""
    if not RUNS_PATH.exists():
        return
    for group_dir in sorted(RUNS_PATH.iterdir()):
        if not group_dir.is_dir():
            continue
        summary = _safe_load_json(group_dir / "summary.json")
        yield (group_dir.name, group_dir, summary)


@router.get("/groups")
def list_groups(era: str | None = None, adapter: str | None = None) -> dict:
    """所有 run group 概览（适合首页 / 总览展示）。

    每个 group 包含：summary + items_count + mtime。
    """
    groups = []
    for name, group_dir, summary in _iter_groups():
        target_adapter = _classify_target_adapter(name)
        era_label = _classify_era(name)
        if era and era_label != era:
            continue
        if adapter and target_adapter != adapter:
            continue
        results_path = group_dir / "results.jsonl"
        items_count = sum(1 for _ in _iter_results_jsonl(results_path)) if results_path.is_file() else 0
        groups.append(
            {
                "name": name,
                "target_adapter": target_adapter,
                "era": era_label,
                "items_count": items_count,
                "summary": summary,
                "mtime": group_dir.stat().st_mtime,
            }
        )
    groups.sort(key=lambda g: g["mtime"], reverse=True)
    return {"total": len(groups), "items": groups}


@router.get("/groups/{group_name}")
def get_group(group_name: str, limit: int = 500) -> dict:
    """单 group 详情：summary + results.jsonl 所有行。"""
    group_dir = RUNS_PATH / group_name
    if not group_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"group not found: {group_name}")

    summary = _safe_load_json(group_dir / "summary.json")
    results = list(_iter_results_jsonl(group_dir / "results.jsonl"))[:limit]

    return {
        "name": group_name,
        "target_adapter": _classify_target_adapter(group_name),
        "era": _classify_era(group_name),
        "summary": summary,
        "results_count": len(results),
        "results": results,
    }


@router.get("/experiments")
def list_experiments(
    adapter: str | None = None,
    qid: str | None = None,
    era: str | None = None,
    verdict: str | None = None,
    limit: int = 200,
) -> dict:
    """把所有 group 的 results 拉平成 experiments 列表（按时间倒序）。

    参数：
        adapter: 筛选 results.jsonl 中的 adapter 字段（mem0/memoryos/graphiti/nomemory/...）
        qid: 题目 ID
        era: bge 时代（before_bge/after_bge/baseline）
        verdict: judge_verdict（correct/wrong/evasive/error）
        limit: 返回上限
    """
    items = []
    for name, group_dir, _summary in _iter_groups():
        era_label = _classify_era(name)
        if era and era_label != era:
            continue
        for row in _iter_results_jsonl(group_dir / "results.jsonl"):
            if adapter and row.get("adapter") != adapter:
                continue
            if qid and row.get("qid") != qid:
                continue
            if verdict and row.get("judge_verdict") != verdict:
                continue
            items.append(
                {
                    "group": name,
                    "era": era_label,
                    **row,
                }
            )
    items.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
    return {
        "total": len(items),
        "limit": limit,
        "items": items[:limit],
    }


@router.get("/latest")
def latest_per_adapter() -> dict:
    """每个 adapter 最近一次的成绩（首页用）。"""
    by_adapter: dict[str, dict] = {}
    for name, group_dir, summary in _iter_groups():
        if not summary:
            continue
        mtime = group_dir.stat().st_mtime
        for adapter_name, stats in summary.items():
            existing = by_adapter.get(adapter_name)
            if existing is None or existing["mtime"] < mtime:
                by_adapter[adapter_name] = {
                    "adapter": adapter_name,
                    "group": name,
                    "era": _classify_era(name),
                    "stats": stats,
                    "mtime": mtime,
                }
    return {"items": list(by_adapter.values())}
