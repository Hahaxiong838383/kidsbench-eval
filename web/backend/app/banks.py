"""题库上传 + CLI 命令桥（子问题 A）。

设计依据 docs/BANKS_API_CONTRACT.md + team 三发评审（grok/gpt/gemini 全票）：
- 转换同步跑（纯函数秒级，不上异步队列）
- 不可变版本化：v_<日期>_<csv内容sha8>，同内容重传返回已有不重写
- 存可写卷 /app/data/banks（不碰 runs-mount:ro）
- CSV 注入防护：issues.csv 单元格 =+-@\\t\\r 前缀加 ' 转义（人用 Excel 打开）
- version 正则白校验防路径穿越；上传题库永不进总榜（与 v01 历史 runs 解耦）
- CLI 桥：按 adapter 注入正确 env/venv/server 依赖（cognee 必带 no-prune+telemetry off）
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api/banks", tags=["banks"])

# 存储根：复用已挂的可写 ./data 卷（assistant.db 同卷），不碰 runs-mount:ro
_BANKS_ROOT = Path(__file__).resolve().parents[1] / "data" / "banks"
# 容器内 KIDSBENCH_BANKS_PATH 可覆盖（部署 /app/data/banks）
import os  # noqa: E402

_BANKS_ROOT = Path(os.environ.get("KIDSBENCH_BANKS_PATH", str(_BANKS_ROOT)))

_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_MAX_ROWS = 3000
_VERSION_RE = re.compile(r"^v_\d{8}_[0-9a-f]{16}$")
_DOWNLOAD_KINDS = {"questions": "questions.jsonl", "issues": "issues.csv", "source": "source.csv"}

# CLI 桥：每 adapter 的 venv + 特殊 env + server 依赖提示
_ADAPTER_RECIPE: dict[str, dict] = {
    "cognee": {"venv": ".venv-cognee", "flag": "--include-cognee",
               "env": "KIDSBENCH_COGNEE_NO_PRUNE=1 TELEMETRY_DISABLED=1",
               "server": "进程内嵌入式（无需起 server）；必带 no-prune+遥测关（否则 hang+error）"},
    "memmachine": {"venv": ".venv", "flag": "--include-memmachine", "env": "",
                   "server": "先 bash scripts/setup_memmachine_server.sh（全 SQLite，8021）"},
    "memobase": {"venv": ".venv-memobase", "flag": "--include-memobase", "env": "",
                 "server": "先 bash scripts/setup_memobase_server.sh（pg0+redis，8019）"},
    "mem0": {"venv": ".venv-mem0", "flag": "--include-mem0", "env": "", "server": "需本地 shim"},
    "memoryos": {"venv": ".venv-memoryos", "flag": "--include-memoryos", "env": "", "server": "需本地 shim"},
    "graphiti": {"venv": ".venv-graphiti", "flag": "--include-graphiti", "env": "",
                 "server": "需 FalkorDB 隧道 16379→QNAP"},
    "letta": {"venv": ".venv-letta", "flag": "--include-letta", "env": "",
              "server": "先 bash scripts/setup_letta_server.sh（pg0，18283）"},
    "hindsight": {"venv": ".venv-hindsight", "flag": "--include-hindsight", "env": "",
                  "server": "embedded pg0（adapter 自起）"},
    "reme": {"venv": ".venv-reme", "flag": "--include-reme", "env": "", "server": "需本地 shim"},
}
_BASELINES = {"nomemory", "fullhistory", "oracle"}  # 随便哪个 venv 都带（不加 --skip-baselines）
_ROUGH_MIN = {"cognee": 210, "memobase": 150, "hindsight": 160, "memmachine": 40,
              "mem0": 40, "memoryos": 40, "graphiti": 60, "letta": 40, "reme": 50}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _csv_safe(cell: str) -> str:
    """防 CSV 公式注入：危险前缀加 ' 转义（Excel/Sheets 不会当公式执行）。"""
    s = "" if cell is None else str(cell)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _convert_csv_bytes(raw: bytes) -> tuple[list[dict], list[dict]]:
    """跑 converter 拿 (questions, issues)。converter 缺 patches/hypotheses 自动兜空。"""
    import sys

    src = Path(__file__).resolve().parents[2] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from kidsbench.questionbank.converter import convert

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        csv_path = tdp / "in.csv"
        csv_path.write_bytes(raw)
        try:
            result = convert(csv_path, tdp / "out", tdp / "nopatch.json", tdp / "nohyp.json")
        except Exception as exc:  # 转换器内部炸（畸形 CSV）→ 400 而非 500
            raise HTTPException(400, f"CSV 转换失败：{type(exc).__name__}: {str(exc)[:200]}") from exc
        issues = [{"qid": i.qid, "kind": i.kind, "detail": i.detail} for i in result.issues]
        return result.questions, issues


def _count_csv_rows(raw: bytes) -> int:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "CSV 必须是 UTF-8 编码") from exc
    return sum(1 for _ in csv.reader(io.StringIO(text)))


def _write_version(version: str, raw: bytes, questions: list[dict],
                   issues: list[dict], original_filename: str) -> dict:
    vdir = _BANKS_ROOT / version
    dist = dict(Counter(q.get("task_type") for q in questions))
    meta = {
        "version": version, "created_at": _now_iso(),
        "question_count": len(questions), "issues_count": len(issues),
        "task_type_dist": dist, "original_filename": original_filename, "status": "validated",
    }
    # 原子写（codex P1#4 并发 race）：写临时目录 → rename 整目录。meta.json 最后写，
    # 是「版本就绪」标志（list/get 都查 meta.json 存在）。
    _BANKS_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _BANKS_ROOT / f".{version}.tmp.{os.getpid()}"
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    (tmp / "source.csv").write_bytes(raw)
    with (tmp / "questions.jsonl").open("w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    with (tmp / "issues.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["题目编号", "问题类型", "详情"])
        for i in issues:
            w.writerow([_csv_safe(i["qid"]), _csv_safe(i["kind"]), _csv_safe(i["detail"])])
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.rename(tmp, vdir)  # 原子；目标已存在（并发对手先到）→ OSError，认对方的
    except OSError:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return meta


def _preview(meta: dict, issues: list[dict], total_rows: int) -> dict:
    healthy = meta["question_count"]
    return {
        **{k: meta[k] for k in
           ("version", "created_at", "question_count", "issues_count",
            "task_type_dist", "original_filename")},
        "issues": issues[:200],
        "health": {"total_rows": total_rows, "healthy": healthy,
                   "skipped": meta["issues_count"]},
    }


def _safe_version(version: str) -> str:
    if not _VERSION_RE.match(version):
        raise HTTPException(400, "非法版本号")
    return version


# ----------------------------------------------------------------- 端点

@router.post("/upload")
async def upload_bank(file: UploadFile) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, f"文件过大（>{_MAX_BYTES // 1024 // 1024}MB）")
    n_rows = _count_csv_rows(raw)
    if n_rows > _MAX_ROWS:
        raise HTTPException(400, f"行数过多（{n_rows} > {_MAX_ROWS}）")

    # 64bit hash（codex P1：sha8=32bit 可工程化碰撞，恶意可让受害者拿到攻击者题库）
    sha16 = hashlib.sha256(raw).hexdigest()[:16]
    version = f"v_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{sha16}"
    vdir = _BANKS_ROOT / version
    if (vdir / "meta.json").exists():
        # 同内容重传 → 返回已有（不可变，不重写）
        meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))
        issues = _read_issues(version)
        return {**_preview(meta, issues, n_rows), "deduplicated": True}

    questions, issues = _convert_csv_bytes(raw)
    if not questions:
        raise HTTPException(422, "转换后零健康题（全部被判为问题题，看 issues）")
    # 文件名净化（codex P2：剔控制字符 + 截断，防 XSS/日志污染；不进路径只进 meta）
    safe_name = re.sub(r"[\x00-\x1f\x7f]", "", file.filename or "upload.csv")[:120]
    meta = _write_version(version, raw, questions, issues, safe_name)
    return _preview(meta, issues, n_rows)


@router.get("")
def list_banks() -> dict:
    if not _BANKS_ROOT.exists():
        return {"banks": []}
    banks = []
    for vdir in _BANKS_ROOT.iterdir():
        mp = vdir / "meta.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        banks.append({k: m.get(k) for k in
                      ("version", "created_at", "question_count",
                       "issues_count", "status", "original_filename")})
    banks.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return {"banks": banks}


@router.get("/{version}")
def get_bank(version: str) -> dict:
    version = _safe_version(version)
    mp = _BANKS_ROOT / version / "meta.json"
    if not mp.exists():
        raise HTTPException(404, "题库版本不存在")
    meta = json.loads(mp.read_text(encoding="utf-8"))
    issues = _read_issues(version)
    return _preview(meta, issues, meta["question_count"] + meta["issues_count"])


@router.get("/{version}/cli")
def gen_cli(version: str, adapters: str = "") -> dict:
    version = _safe_version(version)
    meta_p = _BANKS_ROOT / version / "meta.json"
    if not meta_p.exists():
        raise HTTPException(404, "题库版本不存在")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    names = [a.strip() for a in adapters.split(",") if a.strip()]
    known = set(_ADAPTER_RECIPE) | _BASELINES
    bad = [a for a in names if a not in known]
    if bad:
        raise HTTPException(400, f"未知 adapter: {bad}")
    reals = [a for a in names if a in _ADAPTER_RECIPE]
    if not reals:
        raise HTTPException(400, "至少选一个真实记忆系统（基线随它一起跑）")

    # 命令：先 curl 下载该 bank jsonl 到本地，再逐 adapter 跑
    # （不同 adapter 不同 venv，无法一条命令全跑——生成分段，dev 按需执行）
    pub = "https://kidsbench.cli4.hahaxiong.cc"
    jsonl_url = f"{pub}/api/banks/{version}/download/questions"
    local_jsonl = f"questions/_uploaded_{version}.jsonl"
    lines = [
        "# KidsBench 自助题库评测（在 Air 开发机 kidsbench-eval 根目录执行）",
        f"# 题库 {version}：{meta['question_count']} 题 / {meta['issues_count']} 问题题",
        "# 先设公网 Basic Auth（不在此明文，避免凭据泄漏）：export KIDSBENCH_WEB_AUTH=用户:密码",
        f'curl -s -u "$KIDSBENCH_WEB_AUTH" "{jsonl_url}" -o {local_jsonl}',
        f'echo "下载 $(wc -l < {local_jsonl}) 题"',
        "",
    ]
    for idx, a in enumerate(reals):
        r = _ADAPTER_RECIPE[a]
        if r["server"] and "无需" not in r["server"]:
            lines.append(f'# {a} 依赖：{r["server"]}')
        env = (r["env"] + " ") if r["env"] else ""
        # 第一段带基线，后续段 --skip-baselines（基线只需跑一次，避免重复烧 API）
        skip = "" if idx == 0 else " --skip-baselines"
        run_id = f"upload_{version}_{a}"
        lines.append(
            f'{env}{r["venv"]}/bin/python -m harness.run_eval '
            f'--questions {local_jsonl} --out runs/{run_id} --run-id {a} '
            f'{r["flag"]}{skip} --llm-preset gemini-3-flash --judge-preset qwen-judge'
        )
        lines.append("")
    total_min = sum(_ROUGH_MIN.get(a, 60) for a in reals)
    warn = ""
    if "cognee" in reals:
        warn = "cognee 单跑 3-4h；必带的 no-prune+遥测关已注入命令"
    return {
        "command": "\n".join(lines),
        "note": "不同记忆系统用不同 venv，命令分段——按依赖提示起好 server 后逐段跑。结果不进总榜。",
        "est": {"adapters": len(reals), "questions": meta["question_count"],
                "minutes_rough": total_min,
                "hours_rough": f"~{total_min // 60}-{total_min // 60 + 2}h" if total_min >= 60 else f"~{total_min}min",
                "warn": warn},
    }


@router.get("/{version}/download/{kind}")
def download(version: str, kind: str) -> PlainTextResponse:
    version = _safe_version(version)
    fname = _DOWNLOAD_KINDS.get(kind)
    if fname is None:
        raise HTTPException(404, "未知下载类型")
    fp = _BANKS_ROOT / version / fname
    if not fp.exists():
        raise HTTPException(404, "文件不存在")
    # source.csv 用 text/plain（codex P1：原样 CSV 含 =/+/@ 公式，Excel 打开会执行；
    # issues.csv 已 _csv_safe 转义可保持 text/csv）。全部加 nosniff 防嗅探。
    media = "text/plain; charset=utf-8" if kind == "source" else (
        "text/csv; charset=utf-8" if fname.endswith(".csv") else "application/x-ndjson")
    return PlainTextResponse(
        fp.read_text(encoding="utf-8"), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{version}_{fname}"',
                 "X-Content-Type-Options": "nosniff"},
    )


def _read_issues(version: str) -> list[dict]:
    fp = _BANKS_ROOT / version / "issues.csv"
    if not fp.exists():
        return []
    out = []
    with fp.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out.append({"qid": row.get("题目编号", ""), "kind": row.get("问题类型", ""),
                        "detail": row.get("详情", "")})
    return out
