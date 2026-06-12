# Vestige 核实事实（Phase 0 产出）——🔴 一票否决出局

> 日期：2026-06-12 ｜ 对象：samvallad33/vestige（Rust，FSRS-6 遗忘衰减 + 扩散激活，⭐551 活跃）
> 方法：源码 agent 扫描（/tmp/kb-survey/vestige）+ cc 主线亲手复核致命点 + cargo build 通过
> 结论：**FSRS 衰减时钟不可虚拟化，一票否决**。第三席由 Cognee 替补转正（team 复裁时指定替补，
> 见 COGNEE_VERIFIED_FACTS.md）。

## 一票否决证据（cc 亲手复核，非仅 agent 自报）

它的全部范式卖点 = 按时间遗忘（FSRS-6 spaced repetition）。评测在几分钟内灌入「跨 N 天」
的历史，衰减必须按注入的虚拟时间计算，否则该机制在评测里完全不生效——接它就失去意义。

1. **写入无时间注入位**：`IngestInput`（crates/vestige-core/src/memory/node.rs:272）字段只有
   content/node_type/source/sentiment/tags/valid_from/valid_until——**无 created_at**；
   存储层写入 created_at/last_accessed 一律 `Utc::now()`（storage/sqlite.rs:560,604-606）。
2. **墙钟硬编码规模**：`grep -c "Utc::now()" storage/sqlite.rs` = **61 处**（cc 实测计数），
   覆盖写入/检索/衰减/巩固全路径。
3. **衰减链无法虚拟**：`FSRS.review()` 虽接收外部 `elapsed_days` 参数，但内部更新
   `new_state.last_review = Utc::now()`（fsrs/scheduler.rs:262）——即使单次注入虚拟间隔，
   下一次衰减又从真实时钟起算，虚拟衰减**链**根本建不起来。
4. 仓库自带 `tests/e2e/harness/time_travel.rs` 虚拟时钟环境——**仅测试用，不与生产存储 API 关联**，
   恰好反证作者知道这个需求但没做进 API。

修复需要：IngestInput 加 created_at + scheduler 注入 clock_fn + 存储层 61 处调用点改造 =
**fork 级架构手术**，踩死硬门槛「允许 monkey patch 级小改，不允许架构级不兼容」。
绕过方案（直接 UPDATE SQLite 时间戳）绕开全部 Vestige API，等于没在测它。

## 其他核实点（已查部分，留档供将来复评）

| 核实点 | 结果 | 证据 |
|---|---|---|
| 接口形态 | MCP server（stdio + HTTP JSON-RPC），无 Python bindings | crates/vestige-mcp |
| LLM 依赖 | 纯算法无 LLM（Sanhedrin 验证器可选外接 OpenAI 兼容端点） | tools/cross_reference.rs:164 |
| 中文 | embedding 模型多语言（Nomic v1.5 / Qwen3-0.6b feature）；FTS5 中文按字符分割召回率存疑 | 无显式 ASCII 硬伤 |
| 清场 | 仅单条 delete/purge_node，无按 scope 清场 API | storage/sqlite.rs:1991,2019 |
| metadata 溯源 | KnowledgeNode 无 turn_id 类 metadata 回传 | memory/node.rs |
| 质量体检 | 单 maintainer（高风险）；CI/测试/文档齐全；cargo build --release 本机通过 | .github/workflows |

## 复评触发条件

上游若实现「时间可注入」（IngestInput.created_at + scheduler clock 注入），且仍活跃维护，
可重新进候选池——「拟人化自然遗忘」格子目前仍空缺，它是该格子最对口的候选。
可考虑给上游提 feature request（evaluation/time-travel use case），不投开发资源。

**实验席 spike 选项**（codex 对抗审 P2 补充，不占正式接入名额）：进程级时钟伪造
（libfaketime / DYLD_INSERT_LIBRARIES 包 vestige-mcp 二进制，FAKETIME file 模式逐题推时钟）
不改 vestige 一行代码、保留全部生产 API。可行性未验证（Rust 二进制 + macOS dyld 拦截存疑），
若将来想补「遗忘」格子，先花半天 spike 这条路再说。
