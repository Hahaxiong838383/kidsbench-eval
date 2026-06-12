"""ReMe Phase 0 本机实测（参照 hindsight 版 phase0 打法）。

核实点（源码扫描结论 → 本机真跑验证）：
1.【一票否决】LLM 注入真生效：deepseek base_url 三件套 → 中文对话 summarize
2.【一票否决】中文抽取质量：中文进 → 抽出的记忆是中文且事实正确？
   （主类 vector 路径无 language 参数，行为靠实测）
3. local 向量后端：零外部服务真跑
4. 溯源：add_memory 的 **kwargs metadata（turn_id）→ retrieve 返回里能拿回吗
5. 清场：delete_all 后检索归零
6. token 计量：summarize/retrieve 的 usage 可取吗

embedding 临时用 SiliconFlow BAAI/bge-large-zh-v1.5（OpenAI 兼容 API；
Phase 1 起本地 embedding shim 对齐评测标准 bge-small-zh-v1.5）。

跑法: .venv-reme/bin/python scripts/phase0_reme_verify.py
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

WORK_DIR = "/tmp/reme_phase0"


def load_env() -> dict:
    env = {}
    for line in Path(".env.local").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


async def main() -> int:
    env = load_env()
    shutil.rmtree(WORK_DIR, ignore_errors=True)

    from reme import ReMe

    reme = ReMe(
        working_dir=WORK_DIR,
        default_llm_config={
            "backend": "openai",
            "model_name": "deepseek-v4-flash",
            "api_key": env["KIDSBENCH_DEEPSEEK_API_KEY"],
            "base_url": "https://api.deepseek.com/v1",
        },
        default_embedding_model_config={
            "backend": "openai",
            "model_name": "BAAI/bge-large-zh-v1.5",
            "api_key": env["KIDSBENCH_QWEN_API_KEY"],
            "base_url": "https://api.siliconflow.cn/v1",
            "dimensions": 1024,
        },
        default_vector_store_config={"backend": "local"},
    )
    await reme.start()
    print("✅ [3] ReMe 启动成功（local 后端，零外部服务）")

    # --- 核实 1+2: deepseek 注入 + 中文 summarize
    messages = [
        {"role": "user", "content": "小可！我家的布偶猫叫团子，特别喜欢吃冻干三文鱼",
         "time_created": "2026-06-10 16:00:00"},
        {"role": "assistant", "content": "团子听起来好可爱呀～",
         "time_created": "2026-06-10 16:00:05"},
        {"role": "user", "content": "明天有语文听写，我好紧张",
         "time_created": "2026-06-10 16:30:00"},
    ]
    result = await reme.summarize_memory(messages=messages, user_name="phase0_kid")
    print(f"✅ [1] summarize 完成（deepseek 注入链路通）")
    print(f"    结果类型: {type(result).__name__}")

    # 看抽出的记忆内容（中文质量核实）
    memories = await reme.retrieve_memory(query="猫的名字", user_name="phase0_kid")
    print(f"✅ [2] 检索返回 {len(memories) if hasattr(memories, '__len__') else '?'} 条")
    mem_list = memories if isinstance(memories, list) else getattr(memories, "memories", [memories])
    has_chinese = False
    for m in mem_list[:5]:
        content = getattr(m, "content", None) or (m.get("content", "") if isinstance(m, dict) else str(m))
        print(f"    - {str(content)[:80]}")
        if any("一" <= ch <= "鿿" for ch in str(content)):
            has_chinese = True
    print(f"    中文记忆: {'✅ 是' if has_chinese else '🔴 否（英文化！）'}")

    # --- 核实 4: add_memory metadata 溯源
    node = await reme.add_memory(
        memory_content="孩子最喜欢三角龙",
        user_name="phase0_kid",
        message_time="2026-06-09 10:00:00",
        turn_id="t_042",  # **kwargs metadata
    )
    nid = getattr(node, "memory_id", None) or getattr(node, "id", None)
    print(f"✅ [4a] add_memory 显式写入: id={nid}")
    got = await reme.retrieve_memory(query="三角龙", user_name="phase0_kid")
    got_list = got if isinstance(got, list) else [got]
    for g in got_list[:3]:
        meta = getattr(g, "metadata", None) or (g.get("metadata") if isinstance(g, dict) else None)
        gid = getattr(g, "memory_id", None) or (g.get("memory_id") if isinstance(g, dict) else None)
        print(f"    检索回: id={gid} metadata={meta}")
    print("    [4b] turn_id 是否回传 ↑ 人工确认")

    # --- 核实 5: 清场
    deleted = await reme.delete_all(user_name="phase0_kid") if hasattr(reme, "delete_all") else "无 delete_all 方法"
    print(f"[5] delete_all → {deleted}")
    after = await reme.retrieve_memory(query="三角龙", user_name="phase0_kid")
    after_list = after if isinstance(after, list) else [after]
    print(f"    清场后检索: {len([a for a in after_list if a])} 条（应为 0）")

    await reme.stop() if hasattr(reme, "stop") else None
    print("\nPhase 0 实测脚本完成（token 计量核实见运行日志/单独验证）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
