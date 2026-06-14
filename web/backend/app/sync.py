"""dev198 → QNAP runs 手动同步端点（方案 A · pull）。

背景：评测引擎跑在工作站 dev198，web 后端跑在 QNAP 公网容器。
用户需要时点一下「同步」，容器即经 rsync-over-SSH 主动把 dev198 的
runs/ 拉到本地 runs-dev198/，前端随即看到最新结果。

安全设计：
- 受限 key：dev198 authorized_keys 里这把 key 被 command-locked 到
  `rrsync -ro /home/chuan/kidsbench-eval/runs`，即使私钥泄露也只能
  「只读」拉取那**一个目录**，做不了任何别的（restrict 关掉转发/pty）。
- 私钥经 docker volume `:ro` 挂入容器；请求时复制到 600 临时文件再用，
  绕过 bind-mount uid 导致的 ssh「unprotected key」校验。
- 命令参数全硬编码 + 白名单 source，无用户输入拼接，杜绝注入。
- threading.Lock 防并发 rsync 写同一目录（容器单 worker）。

env（docker-compose 注入，给合理默认）：
    KIDSBENCH_SYNC_SSH_KEY       容器内私钥路径（默认 /secrets/sync_key）
    KIDSBENCH_SYNC_KNOWN_HOSTS   预置 known_hosts（默认 /secrets/known_hosts）
    KIDSBENCH_SYNC_HOST          dev198 公网 host（默认 8.218.26.17）
    KIDSBENCH_SYNC_PORT          dev198 SSH 端口（默认 22198）
    KIDSBENCH_SYNC_USER          dev198 用户（默认 chuan）
    KIDSBENCH_RUNS_PATH_DEV198   同步落地目录（复用 config.runs_path_for）
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time

from fastapi import APIRouter, HTTPException

from . import config

router = APIRouter(prefix="/api/runs", tags=["runs"])

# 容器单 worker：模块级锁足够防并发同步
_sync_lock = threading.Lock()
_last_sync_ts: float = 0.0
_RSYNC_TIMEOUT = 120  # dev198 runs 仅 ~1MB，秒级；给足冗余


@router.post("/sync")
def sync_runs(source: str = "dev198") -> dict:
    """手动触发一次 dev198 → 本地 runs-dev198 同步（rsync over SSH，pull）。

    返回：{ok, source, groups, synced_at}；失败抛对应 HTTP 状态。
    """
    global _last_sync_ts

    # 白名单：air 是后端本地源、无需同步；只允许 dev198
    if source != "dev198":
        raise HTTPException(status_code=400, detail="目前仅支持同步 dev198 数据源")

    if not _sync_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="同步进行中，请稍候再试")
    try:
        dest = config.runs_path_for("dev198")
        dest.mkdir(parents=True, exist_ok=True)

        key_src = os.environ.get("KIDSBENCH_SYNC_SSH_KEY", "/secrets/sync_key")
        if not os.path.isfile(key_src):
            raise HTTPException(status_code=500, detail=f"同步私钥缺失：{key_src}")

        host = os.environ.get("KIDSBENCH_SYNC_HOST", "8.218.26.17")
        port = os.environ.get("KIDSBENCH_SYNC_PORT", "22198")
        user = os.environ.get("KIDSBENCH_SYNC_USER", "chuan")
        known = os.environ.get("KIDSBENCH_SYNC_KNOWN_HOSTS", "/secrets/known_hosts")

        with tempfile.TemporaryDirectory() as td:
            # 复制到 600 临时文件：bind-mount 私钥 uid 可能与容器进程不符，
            # ssh 会拒绝「权限过松」的 key；本地副本规避
            key = os.path.join(td, "id")
            shutil.copyfile(key_src, key)
            os.chmod(key, 0o600)

            if os.path.isfile(known):
                host_opts = f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known}"
            else:
                # 无预置 known_hosts 时降级（TOFU）：仍限定 BatchMode 防交互卡死
                host_opts = "-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/kidsbench_known_hosts"

            ssh_cmd = (
                f"ssh -i {key} -p {port} {host_opts} "
                f"-o ConnectTimeout=10 -o BatchMode=yes"
            )
            # 源写 `user@host:./` → 经 rrsync -ro 解析为锁定根（runs/）的内容。
            # 不用 -a：QNAP volume 文件系统 + 容器 uid 映射不支持 chmod/chown
            #   （rsync 报 "failed to set permissions ... Bad address"），而 web
            #   只读这些 jsonl，无需保留权限/属主/组。保留 -rltz（递归/软链/时间/压缩）
            #   做增量比对，--omit-dir-times 避目录 utime 问题。
            # 注意：rrsync -ro 强制禁用所有 --delete*，故只能增量同步（只增不删）。
            cmd = [
                "rsync", "-rltz",
                "--no-perms", "--no-owner", "--no-group", "--omit-dir-times",
                "-e", ssh_cmd,
                f"{user}@{host}:./",
                f"{dest}/",
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=_RSYNC_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=504, detail=f"同步超时（{_RSYNC_TIMEOUT}s）")
            except FileNotFoundError:
                raise HTTPException(status_code=500, detail="容器内缺少 rsync（需重建镜像）")

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            raise HTTPException(status_code=502, detail="rsync 失败：" + " / ".join(tail))

        _last_sync_ts = time.time()
        groups = sum(1 for p in dest.iterdir() if p.is_dir()) if dest.exists() else 0
        return {
            "ok": True,
            "source": "dev198",
            "groups": groups,
            "synced_at": _last_sync_ts,
        }
    finally:
        _sync_lock.release()
