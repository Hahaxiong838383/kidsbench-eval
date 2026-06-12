"""Memobase Phase 0 尸检实测脚本（可重跑）。

前置（已由尸检流程搭好，重跑时按此恢复）：
  1. pg0 实例 kidsbench-memobase（port 5434，库 memobase + vector 扩展）
  2. redis-server --port 6399 --daemonize yes
  3. server：cd /tmp/kb-survey/memobase/src/server/api &&
     DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5434/memobase \
     REDIS_URL=redis://localhost:6399/0 ACCESS_TOKEN=kb-phase0-secret PROJECT_ID=kidsbench \
     .venv-memobase/bin/uvicorn api:app --port 8019
     （api 目录的 config.yaml：language=zh + deepseek 注入 + enable_event_embedding=false）

核查点（对应 Phase 0 通用 10 项中能在客户端侧实测的）：
  A 中文写入→画像全链路    B flush(sync=True) 同步语义
  C 画像读取形态           D 事件 + 溯源字段
  E created_at 虚拟时钟注入 F 物理清场
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from memobase import ChatBlob, MemoBaseClient

BASE = "http://127.0.0.1:8019"
TOKEN = "kb-phase0-secret"  # 本地尸检临时 token，无敏感性

# created_at 是字符串字段（pydantic 校验 string_type），格式同 ReMe 的 time_created
T_MINUS_7D = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

CHATS = [
    {"role": "user", "content": "我家的布偶猫叫团子，特别喜欢吃冻干三文鱼", "created_at": T_MINUS_7D},
    {"role": "assistant", "content": "团子听起来好可爱！它今年几岁啦？", "created_at": T_MINUS_7D},
    {"role": "user", "content": "两岁啦。对了我最近在准备数学期中考试，分数应用题总是出错，有点烦", "created_at": T_MINUS_7D},
    {"role": "assistant", "content": "分数应用题确实需要多练，我们可以一起整理错题", "created_at": T_MINUS_7D},
]


def main() -> int:
    client = MemoBaseClient(project_url=BASE, api_key=TOKEN)
    assert client.ping(), "server ping 失败"
    print("✅ server ping 通过")

    uid = client.add_user({"name": "phase0_zh_kid"})
    user = client.get_user(uid)
    print(f"✅ 用户创建 {uid}")

    # A+E: 中文写入（带 7 天前时间戳——虚拟时钟注入位）
    t0 = time.monotonic()
    bid = user.insert(ChatBlob(messages=CHATS))
    t_insert = time.monotonic() - t0
    print(f"✅ insert 完成 {t_insert:.2f}s blob={bid}")

    # B: 同步 flush（异步 worker 风险的关键验证——返回后画像必须立刻可读）
    t0 = time.monotonic()
    user.flush(sync=True)
    t_flush = time.monotonic() - t0
    print(f"✅ flush(sync=True) 完成 {t_flush:.2f}s")

    # C: 画像读取（中文断言）
    profiles = user.profile()
    print(f"--- 画像 {len(profiles)} 条 ---")
    zh_hit = pet_hit = False
    for p in profiles:
        line = f"{p.topic}/{p.sub_topic}: {p.content}"
        print(" ", line)
        if any("一" <= ch <= "鿿" for ch in line):
            zh_hit = True
        if "团子" in line:
            pet_hit = True
    assert profiles, "❌ flush 后画像为空（异步 worker 没等住或抽取失败）"
    assert zh_hit, "❌ 画像不是中文（language=zh 没生效）"
    print(f"✅ 画像中文={zh_hit} 实体『团子』命中={pet_hit}")

    # D: 事件 + 溯源/时间字段
    events = user.event()
    print(f"--- 事件 {len(events)} 条 ---")
    for e in events[:3]:
        print(" ", str(e)[:160])

    # F: 物理清场
    assert client.delete_user(uid), "delete_user 返回失败"
    try:
        client.get_user(uid).profile()
        print("❌ 删除后仍可读画像——清场不彻底")
        return 1
    except Exception as exc:
        print(f"✅ 清场后读取按预期失败：{type(exc).__name__}")

    print("\n=== 尸检实测结论：核心链路全通 ===")
    print(f"耗时 insert={t_insert:.2f}s flush(sync)={t_flush:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
