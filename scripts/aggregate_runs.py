#!/usr/bin/env python3
"""KidsBench 多轮重跑结果聚合统计脚本。

用法：
    python scripts/aggregate_runs.py runs/rerun_20251005_123456

功能：
- 扫描 BASE_DIR/*/summary.json + results.jsonl
- 聚合每个 adapter 的 correct/wrong/evasive/error 统计（均值、样本标准差、极值）
- 统计每 (adapter, qid) 的 verdict 稳定性，标记不稳定题目
- 输出 aggregate.json + 中文终端表格报告

设计约束（严格遵守）：
- 纯标准库（json, pathlib, statistics, collections, sys, typing）
- 不可变风格：函数返回新对象，不修改入参
- 小函数拆分（每个 <50 行）
- 显式异常处理 + 中文告警，不静默失败
- 中文 docstring + 关键处注释
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# baseline adapter 名集合：每次跑被测 adapter 时都会附带产出，
# 聚合时每轮只能采信一次，否则 N 轮 M 家会让 baseline 样本通胀到 N*M（伪重复）。
BASELINES = frozenset({"nomemory", "fullhistory", "oracle"})

# run_dir 命名规则：<target_adapter>_r<round>，用于识别每个目录的主测 adapter + 轮次
_RUN_DIR_RE = re.compile(r"^(.+)_r(\d+)$")


def load_json_safe(path: Path) -> dict[str, Any] | None:
    """安全加载 JSON，失败时打印中文警告并返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        print(f"警告: 跳过坏 JSON {path}: {e}", file=sys.stderr)
        return None


def load_results_verdicts(results_path: Path) -> dict[str, dict[str, str]]:
    """从 results.jsonl 加载 {adapter: {qid: verdict}} 映射（单轮内）。"""
    verdicts: dict[str, dict[str, str]] = defaultdict(dict)
    if not results_path.exists():
        return {}
    try:
        for line in results_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("qid")
            adapter = rec.get("adapter")
            verdict = rec.get("judge_verdict")
            if qid and adapter and verdict:
                verdicts[adapter][qid] = str(verdict)
        return dict(verdicts)
    except Exception as e:  # noqa: BLE001
        print(f"警告: 解析 results.jsonl 失败 {results_path}: {e}", file=sys.stderr)
        return {}


def group_runs_by_round(base_dir: Path) -> dict[int, list[tuple[str, Path]]]:
    """按轮次分组所有 run 目录。返回 {round: [(target_adapter, summary_path), ...]}。

    目录名形如 mem0_r0；无法解析的归到第 0 轮（容错）。返回新对象。
    """
    rounds_map: dict[int, list[tuple[str, Path]]] = defaultdict(list)
    for summary_path in sorted(base_dir.glob("*/summary.json")):
        name = summary_path.parent.name
        m = _RUN_DIR_RE.match(name)
        target, rnd = (m.group(1), int(m.group(2))) if m else (name, 0)
        rounds_map[rnd].append((target, summary_path))
    return {k: list(v) for k, v in rounds_map.items()}


def _extract_metrics(m: dict[str, Any]) -> dict[str, int]:
    """从单家 summary 项抽取标准指标。返回新 dict。"""
    return {k: int(m.get(k, 0)) for k in ("correct", "wrong", "evasive", "error", "total")}


def scan_runs(base_dir: Path) -> tuple[dict[str, list[dict[str, int]]], dict[tuple[str, str], list[str]]]:
    """扫描 BASE_DIR 聚合各 run，消除 baseline 伪重复。

    关键：被测 adapter 每轮跑会附带产出 3 个 baseline，N 轮 M 家会让
    baseline 被记 N*M 次（伪重复，压低标准差/拉偏均值）。本函数按轮分组，
    每轮 baseline 只从「第一个成功 load 且含 baseline」的目录采信一次；
    非 baseline 只采信该目录的主测 adapter 自身，丢弃残留。

    返回 (adapter -> [metrics, ...], (adapter,qid) -> [verdict, ...])，均为新对象。
    """
    adapter_metrics: dict[str, list[dict[str, int]]] = defaultdict(list)
    q_verdicts: dict[tuple[str, str], list[str]] = defaultdict(list)

    for _rnd, runs in sorted(group_runs_by_round(base_dir).items()):
        baseline_taken = False  # 本轮 baseline 是否已采信
        for target, summary_path in sorted(runs):
            summary = load_json_safe(summary_path)
            if summary is None:
                continue
            res_verdicts = load_results_verdicts(summary_path.parent / "results.jsonl")
            take_baseline = not baseline_taken
            for adapter, m in summary.items():
                if not isinstance(m, dict):
                    continue
                if adapter in BASELINES:
                    if not take_baseline:
                        continue  # baseline 本轮已采信，跳过避免伪重复
                elif adapter != target:
                    continue  # 非主测 adapter 的残留，跳过
                adapter_metrics[adapter].append(_extract_metrics(m))
                for qid, v in res_verdicts.get(adapter, {}).items():
                    q_verdicts[(adapter, qid)].append(v)
            if take_baseline and any(a in BASELINES for a in summary):
                baseline_taken = True  # 仅当本目录确含 baseline 才标记已采信

    return dict(adapter_metrics), {k: list(v) for k, v in q_verdicts.items()}


def _safe_stdev(values: list[int]) -> float | None:
    """样本标准差。n<2 时数学上无定义，返回 None（渲染为 N/A，避免误读为「超稳定」）。"""
    if len(values) < 2:
        return None
    try:
        return statistics.stdev(values)
    except statistics.StatisticsError:
        return None


def compute_stats(values: list[int]) -> dict[str, Any]:
    """对一列指标值计算均值/标准差/min/max/原始列表/n。返回新 dict。"""
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "stdev": None, "min": 0, "max": 0, "values": [], "n": 0}
    vals = list(values)  # 拷贝
    mean = float(statistics.mean(vals))
    stdev = _safe_stdev(vals)
    return {
        "mean": mean,
        "stdev": stdev,
        "min": min(vals),
        "max": max(vals),
        "values": vals,
        "n": n,
    }


def aggregate_adapter_metrics(
    raw: dict[str, list[dict[str, int]]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """把每 adapter 的多轮 metrics 聚合成统计量。返回全新结构。"""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for adapter, runs in raw.items():
        per_key: dict[str, list[int]] = defaultdict(list)
        for run_m in runs:
            for key in ("correct", "wrong", "evasive", "error"):
                per_key[key].append(run_m.get(key, 0))
        stats = {k: compute_stats(vs) for k, vs in per_key.items()}
        result[adapter] = stats
    return result


def aggregate_question_stability(
    raw: dict[tuple[str, str], list[str]]
) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, str]]]:
    """聚合每题稳定性。返回 (按adapter分组的详情, 不稳定列表)。均为新对象。"""
    by_adapter: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    unstable: list[dict[str, str]] = []

    for (adapter, qid), verdicts in sorted(raw.items()):
        if not verdicts:
            continue
        dist = Counter(verdicts)
        stable = len(dist) == 1
        dist_str = ", ".join(f"{v}×{c}" for v, c in sorted(dist.items()))
        entry = {
            "n": len(verdicts),
            "stable": stable,
            "distribution": dist_str,
            "verdicts": list(verdicts),  # 拷贝
        }
        by_adapter[adapter][qid] = entry
        if not stable:
            unstable.append({"adapter": adapter, "qid": qid, "distribution": dist_str})

    return dict(by_adapter), unstable


def fmt_num(x: float | None) -> str:
    """格式化数字：None→N/A；整数去 .00；否则保留最多 2 位小数。"""
    if x is None:
        return "N/A"
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def format_adapter_table(adapter_agg: dict[str, dict[str, dict[str, Any]]]) -> str:
    """生成中文 adapter 级统计表格字符串。"""
    lines = ["=== Adapter 级聚合统计 (每轮 summary 均值±样本标准差) ==="]
    # 排序：先 memory adapters，再 baselines
    order = ["mem0", "memoryos", "graphiti", "nomemory", "fullhistory", "oracle"]
    sorted_adapters = sorted(
        adapter_agg.keys(), key=lambda a: (order.index(a) if a in order else 99, a)
    )

    for adp in sorted_adapters:
        stats = adapter_agg[adp]
        for metric in ("correct", "wrong", "evasive", "error"):
            if metric not in stats:
                continue
            s = stats[metric]
            n = s["n"]
            note = " (样本不足)" if n < 2 else ""
            mean_s = fmt_num(s["mean"])
            stdev_s = fmt_num(s["stdev"])
            line = (
                f"{adp:12s} {metric:8s} "
                f"{mean_s}±{stdev_s} (min{s['min']} max{s['max']}, n={n}){note}"
            )
            lines.append(line)
    if len(lines) == 1:
        lines.append("(无数据)")
    return "\n".join(lines)


def format_unstable_table(unstable: list[dict[str, str]]) -> str:
    """生成不稳定题目中文报告。"""
    lines = ["=== 每题稳定性报告（仅显示 N 轮 verdict 不一致的题目） ==="]
    if not unstable:
        lines.append("✅ 全部 (adapter, qid) 在各轮中 judge_verdict 完全一致，稳定性良好。")
        return "\n".join(lines)

    lines.append(f"⚠️  发现 {len(unstable)} 个不稳定 (adapter, qid)：")
    lines.append("这些题在不同轮次表现不一致，可能是记忆系统非确定性导致，值得重点排查：\n")
    for item in unstable:
        lines.append(f"  - {item['adapter']:12s} / {item['qid']:6s} : {item['distribution']}")
    return "\n".join(lines)


def main() -> int:
    """主入口。"""
    if len(sys.argv) < 2:
        print("用法: python scripts/aggregate_runs.py <BASE_DIR>", file=sys.stderr)
        print("  BASE_DIR 例如: runs/rerun_20251005_123456", file=sys.stderr)
        return 2

    base_dir = Path(sys.argv[1]).resolve()
    if not base_dir.exists() or not base_dir.is_dir():
        print(f"错误: BASE_DIR 不存在或不是目录: {base_dir}", file=sys.stderr)
        return 1

    print(f"[aggregate] 扫描目录: {base_dir}", flush=True)

    adapter_raw, q_raw = scan_runs(base_dir)
    if not adapter_raw:
        print("警告: 未在 BASE_DIR 下找到任何有效的 summary.json", file=sys.stderr)
        # 仍继续写空报告

    adapter_agg = aggregate_adapter_metrics(adapter_raw)
    q_stability, unstable = aggregate_question_stability(q_raw)

    # 组装最终 JSON（可扩展 meta）
    num_run_dirs = len(list(base_dir.glob("*/summary.json")))
    meta = {
        "base_dir": str(base_dir),
        "scanned_run_dirs": num_run_dirs,
        "adapters_found": sorted(adapter_raw.keys()),
        "total_adapter_samples": sum(len(v) for v in adapter_raw.values()),
    }

    full_report = {
        "meta": meta,
        "adapter_metrics": adapter_agg,
        "question_stability": q_stability,
        "unstable_questions": unstable,
    }

    out_json = base_dir / "aggregate.json"
    try:
        out_json.write_text(
            json.dumps(full_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[aggregate] 已写入: {out_json}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"错误: 无法写入聚合报告 {out_json}: {e}", file=sys.stderr)
        return 1

    # 终端中文报告
    print("\n" + format_adapter_table(adapter_agg))
    print("\n" + format_unstable_table(unstable))
    print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
