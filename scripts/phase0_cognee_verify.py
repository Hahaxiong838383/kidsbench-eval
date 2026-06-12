"""Cognee Phase 0 实测脚本（可重跑）。

前置：embedding shim 在 18230；.venv-cognee（cognee 0.5.1 + mistralai<2 钉版本）。
核查点：A LLM/embedding 注入  B 中文实体抽取（默认英文 prompt vs custom_prompt 中文指令 A/B）
        C 多跳检索 GRAPH_COMPLETION  D 物理清场 prune  E 幂等观察
用法：.venv-cognee/bin/python scripts/phase0_cognee_verify.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# ---- 注入配置必须在 import cognee 前 ----
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.local"
_GMKEY = ""
for line in _ENV_PATH.read_text().splitlines():
    if line.startswith("KIDSBENCH_GEMINI_API_KEY="):
        _GMKEY = line.split("=", 1)[1].strip()
        break
if not _GMKEY:
    raise SystemExit("没找到 KIDSBENCH_GEMINI_API_KEY")

os.environ.update({
    # 结构化输出模式三连坑（实测）：默认 tools 模式下
    #   deepseek thinking → "Thinking mode does not support this tool_choice"
    #   gemini-2.5-flash 经 proxy → "Malformed function call"（proxy FC 转译层）
    # → LLM_INSTRUCTOR_MODE=json_mode 绕开 tool_choice，deepseek 直接可用
    "LLM_PROVIDER": "openai",
    "LLM_MODEL": "openai/gemini-2.5-flash",
    "LLM_ENDPOINT": "http://23.226.135.149:4000/v1",
    "LLM_API_KEY": _GMKEY,
    "LLM_INSTRUCTOR_MODE": "json_mode",
    # provider 不能写 openai：tokenizer 选择按 provider 名走 tiktoken 映射，
    # bge 模型名映射不了直接 KeyError；写 custom 走 HF→TikToken 默认兜底链
    "EMBEDDING_PROVIDER": "custom",
    "EMBEDDING_MODEL": "openai/BAAI/bge-small-zh-v1.5",
    "EMBEDDING_ENDPOINT": "http://127.0.0.1:18230/v1",
    "EMBEDDING_API_KEY": "dummy-local",
    "EMBEDDING_DIMENSIONS": "512",
    # PyPI 0.5.1 无 ladybug（那是 main 分支新 provider），嵌入式图库用 kuzu
    "GRAPH_DATABASE_PROVIDER": "kuzu",
    "VECTOR_DB_PROVIDER": "lancedb",
    # 单用户评测，关多用户访问控制（ladybug+kuzu handler 组合不支持，启动报 OSError）
    "ENABLE_BACKEND_ACCESS_CONTROL": "false",
})

# ---- monkey patch：强制 instructor JSON 模式（必须在 import cognee 前）----
# 上游 quirk（openai/adapter.py:82）：instructor_mode 只在模型名含 "gpt-5" 时生效，
# 其他模型一律 instructor 默认 TOOLS 模式 → deepseek thinking 拒 tool_choice /
# gemini 经 proxy 吐 malformed_function_call。JSON 模式（response_format）绕开。
import instructor  # noqa: E402

_orig_from_litellm = instructor.from_litellm


def _patched_from_litellm(fn, mode=None, **kw):
    return _orig_from_litellm(fn, mode=instructor.Mode.JSON, **kw)


instructor.from_litellm = _patched_from_litellm

import cognee  # noqa: E402
from cognee.modules.search.types import SearchType  # noqa: E402

CORPUS = (
    "小川家里养了一只布偶猫，名字叫团子，团子特别喜欢吃冻干三文鱼。"
    "小川最近在准备数学期中考试，分数应用题总是出错。"
    "上周小川和妈妈一起去宠物店给团子买了一个新的猫爬架。"
)

ZH_PROMPT = """你是知识图谱抽取专家。从用户文本中抽取实体和关系，构建知识图谱。
铁律：所有实体名、关系名必须使用与原文相同的语言（中文文本输出中文实体），
绝对禁止把实体翻译成英文。实体名保持原文表述（如「团子」「小川」「数学期中考试」）。
按指定的 JSON schema 输出。"""


async def graph_entity_names() -> list[str]:
    from cognee.infrastructure.databases.graph import get_graph_engine

    engine = await get_graph_engine()
    nodes, _edges = await engine.get_graph_data()
    names = []
    for _nid, props in nodes:
        name = (props or {}).get("name") or ""
        if name and props.get("type") in ("Entity", "EntityType"):
            names.append(name)
    return names


def zh_ratio(names: list[str]) -> float:
    if not names:
        return 0.0
    zh = sum(1 for n in names if any("一" <= ch <= "鿿" for ch in n))
    return zh / len(names)


async def run_round(label: str, custom_prompt: str | None) -> list[str]:
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)
    await cognee.add(CORPUS, "phase0_zh")
    await cognee.cognify(["phase0_zh"], custom_prompt=custom_prompt)
    names = await graph_entity_names()
    print(f"--- [{label}] 实体节点 {len(names)} 个，中文占比 {zh_ratio(names):.0%} ---")
    print("  ", "、".join(names[:15]))
    return names


async def main() -> int:
    # B1: 默认英文 prompt
    names_default = await run_round("默认英文 prompt", None)

    # B2: 中文 custom_prompt
    names_zh = await run_round("中文 custom_prompt", ZH_PROMPT)

    # C: 多跳检索（中文问题，2-hop）
    results = await cognee.search(
        "团子的猫爬架是在哪里买的？", query_type=SearchType.GRAPH_COMPLETION, top_k=5,
    )
    print("--- GRAPH_COMPLETION 回答 ---")
    for r in results[:2]:
        print("  ", str(r)[:200])
    answer_text = " ".join(str(r) for r in results)

    # D: 清场（metadata=True 连用户库一起删，删后 search 抛 DatabaseNotCreatedError
    # ——这正是物理清除成立的证据；评测中清场后会重新 setup）
    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)
    from cognee.infrastructure.databases.exceptions.exceptions import (
        DatabaseNotCreatedError,
    )

    try:
        after = await cognee.search("团子", query_type=SearchType.CHUNKS, top_k=3)
        cleared = len(after) == 0
        print(f"清场后 CHUNKS 检索: {len(after)} 条（应为 0）")
    except DatabaseNotCreatedError:
        cleared = True
        print("清场后检索抛 DatabaseNotCreatedError（库已物理删除）✅")

    print("\n=== 判定 ===")
    print(f"默认 prompt 中文实体占比: {zh_ratio(names_default):.0%}")
    print(f"中文 prompt 中文实体占比: {zh_ratio(names_zh):.0%}")
    print(f"多跳回答含『宠物店』: {'宠物店' in answer_text}")
    ok = zh_ratio(names_zh) >= 0.8 and cleared
    print("结论:", "🟢 中文可经 custom_prompt 修复" if ok else "🔴 中文风险未解除")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
