# ReMe 核实事实（Phase 0 产出）

> 日期：2026-06-12 ｜ 版本锁定：reme-ai **0.3.1.10**（.venv-reme，agentscope==1.0.20 必须钉版本）
> 方法：源码 agent 扫描（/tmp/kb-survey/ReMe）+ 本机实测三轮（deepseek + SiliconFlow embedding 真跑）
> 实测脚本：`scripts/phase0_reme_verify.py`（v1）+ /tmp/reme_zh_test.py（中文注入因果）+ /tmp/reme_final_test.py（收尾）
> 背景：源码重盘点裁决（2026-06-12）ReMe 排第一——唯一原生中文意图 + 接入最轻 + agentscope-ai 持续维护

## 红绿灯总表

| # | 核实点 | 结果 | 证据 |
|---|---|---|---|
| 1 | **LLM 注入【一票否决】** | 🟢 | `default_llm_config` 三件套（backend=openai/model_name/api_key/base_url）→ deepseek-v4-flash 实测 summarize/retrieve 全链路通 |
| 2 | **中文可用【一票否决】** | 🟢（需注入）| ⚠️ vector 路径记忆 prompt 无 `_zh` 版（只有 File 路径有），默认抽出**英文记忆**（实测「Pet: 布偶猫 named 团子」）。**修法实证**：monkey patch `PromptHandler.prompt_format` 单点追加中文输出指令 → 记忆完全中文化（「zh_kid养了一只布偶猫，名字叫团子」），零 fork、实体精确 |
| 3 | local 向量后端 | 🟢 | `default_vector_store_config={"backend":"local"}` 纯 Python+JSONL，零外部服务实测 |
| 4 | 检索溯源 | 🟢 | `retrieve_memory(return_dict=True)` 返回 `retrieved_nodes`（原始节点）；节点字段含 **`ref_memory_id`（专用引用字段，实测回传）→ adapter 写入时存 turn_id**。注意：add_memory 的 **kwargs 自定义字段不回传（实测），必须用 ref_memory_id |
| 5 | 清场 | 🟢 | `delete_all()`（无参全清）实测后检索归零；按题隔离用独立 user_name + 每题 delete_all |
| 6 | token 计量 | 🟡 | return_dict 无 usage 字段（实测）；与 mem0/graphiti 同级「未上报」，榜单 token_note 机制已兼容 |
| 7 | embedding | 🟡 | 仅 OpenAI 兼容 API（无本地 HF 直载）。Phase 0 临时用 SiliconFlow `BAAI/bge-large-zh-v1.5`；**Phase 1 必须起本地 embedding shim**（FastAPI 包 bge-small-zh-v1.5 成 /v1/embeddings）对齐评测标准 |
| 8 | 读取范式 | 📌 | retrieve 是 **agentic 读取**（LLM 工具循环，实测 ~20s/次 + 合成 answer）——**晚绑定型**，范式登记与 hindsight-reflect 同类但机制不同（工具循环 vs 一次合成） |

## 工程事实（接入要记住的）

1. **依赖钉版本**：`pip install reme-ai agentscope==1.0.20`——agentscope 新版缺 `agentscope.token` 模块直接 import 炸
2. **中文注入 patch**（adapter 初始化时装）：
   ```python
   from reme.core.prompt_handler import PromptHandler
   _orig = PromptHandler.prompt_format
   def patched(self, prompt_name, **kw):
       out = _orig(self, prompt_name, **kw)
       return out + ZH_DIRECTIVE if isinstance(out, str) else out
   PromptHandler.prompt_format = patched
   ```
   长期正解：给上游提 PR 补 vector 路径 `_zh` prompt（PromptHandler 语言后缀机制现成）
3. **写入两条路**：`summarize_memory(messages)`（LLM 自动抽取，对应评测 write 逐 turn 喂）/ `add_memory(memory_content, ref_memory_id=turn_id)`（显式写，Oracle 注入用）
4. retrieve ~20s/次 → 149 题 read ≈ 50min，可接受；write（summarize 每题一次多轮工具循环）耗时待 smoke 实测
5. summarize 的 messages 需带 `time_created`（"YYYY-MM-DD HH:MM:SS"）——virtual_clock 对接点

## 给 Phase 1/2 的输入

- Phase 1：venv 固化（requirements 钉版本）+ **本地 embedding shim**（bge-small-zh-v1.5 → OpenAI /v1/embeddings，~60 行 FastAPI，以后所有「仅 API embedding」的系统共用）
- Phase 2 adapter 设计要点：mode 单一（agentic read）；write=summarize per-turn 批；ref_memory_id 存 turn_id；clear=per-user delete + 题间隔离；中文 patch 装载在 adapter setup；范式登记「agentic 检索·晚绑定变体」
- 范式覆盖地图更新：ReMe 主场 = T2 跨会话综合（agentic read 多记忆整合）+ T1
