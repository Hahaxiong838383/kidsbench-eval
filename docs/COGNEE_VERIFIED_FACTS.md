# Cognee 核实事实（Phase 0 产出）

> 日期：2026-06-12 ｜ 版本锁定：PyPI **0.5.1** + **mistralai>=1.5,<2 必须钉版本**｜ .venv-cognee（py3.12）
> 方法：源码 agent 扫描（/tmp/kb-survey/cognee，clone a22320c 2026-06-08）+ 本机实测（中文 A/B 真跑）
> 实测脚本：`scripts/phase0_cognee_verify.py`
> 背景：**Vestige 一票否决后的替补转正**（见 VESTIGE_VERIFIED_FACTS.md）。多跳/图谱范式候选，
> 与 graphiti 构成范式内对照（pipeline 双库 vs temporal KG）。⭐17,800、当日仍 push（cc gh 核实）。

## 红绿灯总表

| # | 核实点 | 结果 | 证据 |
|---|---|---|---|
| 1 | **LLM 注入【一票否决】** | 🟢（带 2 坑） | env `LLM_PROVIDER/LLM_MODEL/LLM_ENDPOINT/LLM_API_KEY`（litellm 路由）。坑见工程事实 #2/#3 |
| 2 | **中文可用【一票否决】** | 🟢 **A/B 实测修复** | 默认英文 few-shot prompt → 实体中文占比 53%（EntityType 节点英文 person/animal/food）；`cognify(custom_prompt=中文铁律 prompt)` → **100% 全中文**（人物/宠物/食物/事件/学习内容）。官方参数级修复，零 fork |
| 3 | **物理清场【一票否决】** | 🟢 | `prune_data()+prune_system(metadata=True)` 全局物理删（删后 search 抛 DatabaseNotCreatedError=库真没了）。**无按 dataset 局部 prune**——评测协议本就逐题全清+重 setup，可接受 |
| 4 | 嵌入式部署 | 🟢 | kuzu（嵌入式图库）+ LanceDB（本地文件向量库）+ SQLite 元数据，零外部服务（0.5.1 无 ladybug，那是 main 新 provider）。需 `ENABLE_BACKEND_ACCESS_CONTROL=false`（单用户评测） |
| 5 | embedding 注入 | 🟢（带 1 坑） | env EMBEDDING_* → 本地 shim 512d 实测通。坑见工程事实 #4 |
| 6 | 多跳检索（范式卖点） | 🟢 | `search(GRAPH_COMPLETION, neighborhood_depth=k)`：向量 seed top-k → k-hop 邻域投影 + 距离衰减（brute_force_triplet_search.py:49-116）。实测 2-hop 中文问答「团子的猫爬架在哪买的」→「宠物店」正确 |
| 7 | 溯源 | 🔴→🟡wrapped | search 返回纯文本无 metadata/源引用；add 无 turn_id 注入位（node_set 标签可标 turn）→ adapter wrapped：node_set=turn 标签 + CHUNKS 检索对照。**溯源是它最弱项**（Attribution 指标会吃亏，如实标注） |
| 8 | 虚拟时钟 | 🔴 受限 | DataPoint.created_at 自动生成无注入口（DataPoint.py:45-49）；标准 search 无时间过滤。`temporal_cognify=True` pipeline 有事件时间维度（待 Phase 2 评估）。**对多跳主场题型影响小**（多跳不靠 recency），登记 capability declared |
| 9 | 写入幂等性 | ⚠️ 非幂等 | 实体按 node_id（uuid5(name)）合并，但重复 add+cognify 产生新 chunk/三元组 → adapter content hash 查重 |
| 10 | token 计量 | 🟡 | session usage 用 char/4 估算（usage_tracking.py:45-82）非精确 → 榜单「未上报」或 AOP 拦截 |
| 11 | 后台自动行为 | 🟢 可控 | cognify 显式触发（run_in_background 默认 False），无后台 worker |

## 工程事实（接入要记住的）

1. **依赖钉版本**：`pip install cognee "mistralai>=1.5,<2"`——mistralai 2.x 改了包结构，
   `from mistralai import Mistral` 直接 ImportError 炸整个 import 链（实测）。
2. **instructor 模式上游 quirk（最大坑）**：`LLM_INSTRUCTOR_MODE` 配置**只在模型名含 "gpt-5" 时生效**
   （openai/adapter.py:82 的 if "gpt-5" in model 分支），其他模型一律 instructor 默认 TOOLS 模式 →
   deepseek thinking 报 "Thinking mode does not support this tool_choice"、gemini 经 proxy 报
   malformed_function_call，5 连重试全败。**修法（实测）**：import cognee 前 monkey patch
   `instructor.from_litellm` 强制 `mode=instructor.Mode.JSON`（脚本里有现成实现）。
3. **结构化抽取模型选择**：JSON 模式下 gemini-2.5-flash（经 proxy）实测稳定；deepseek thinking 待 JSON 模式复测。
4. **embedding provider 命名坑**：`EMBEDDING_PROVIDER=openai` 会按模型名查 tiktoken 映射，bge 模型名
   KeyError 炸；**写 `custom`** → HF tokenizer 尝试失败 → TikToken 默认兜底链（LiteLLMEmbeddingEngine.py:188-230）。
   模型写 `openai/BAAI/bge-small-zh-v1.5`（litellm 前缀路由）+ ENDPOINT 指 shim。
5. **中文 custom_prompt 模板**（实测 100% 中文实体）：核心三句——实体/关系名必须与原文同语言、
   禁止翻译成英文、实体名保持原文表述。存档在 `scripts/phase0_cognee_verify.py` 的 ZH_PROMPT。
6. cognify 耗时：3 句语料 + 建图 ≈ 1-2 min（含多次 LLM 调用）——149 题全量预算要先估。

## 给 Phase 1/2 的输入

- Phase 1：venv 固化（cognee==0.5.1 + mistralai<2 钉死）；ZH_PROMPT 收进 configs/
- Phase 2 adapter：write=add(text, dataset, node_set=[turn_id])（content hash 查重）→ 题末统一 cognify(custom_prompt=ZH_PROMPT)；read=search(GRAPH_COMPLETION, neighborhood_depth=2) + CHUNKS 对照；clear=prune 全清 + setup 重建；monkey patch 装载在 adapter setup
- 范式登记：**pipeline 双库多跳（vector seed + k-hop 邻域投影）**，主场 = 多跳联想/干扰召回；与 graphiti 范式内对照
- ⚠️ 能力矩阵如实标注：溯源 wrapped 弱 / 虚拟时钟 declared 受限 / token simulated
