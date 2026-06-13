# AI 助手同步清单（新功能/新系统强制同步）

> 川哥钦定（2026-06-13）：**所有新增功能/新接入系统，必须同步给网站 AI 助手**。
> 助手的知识 = `web/backend/app/knowledge/KNOWLEDGE.md`（手册，system prompt 前缀）
> + `web/backend/app/assistant_tools.py` 的 `DOC_WHITELIST`（read_doc 可查文档）。
> 不同步 = 助手对新功能一问三不知，等于交付不完整。

## 触发时机

任何「新功能上线」或「新记忆系统接入」的收尾流程，**部署前**必须跑本清单。
与 `NEW_SYSTEM_CHECKLIST.md`（展示位同步）平行——那个管页面显示，这个管助手知识。

## 同步三步（缺一不可）

### 1. 更新 KNOWLEDGE.md（手册，让助手"知道"）
按功能类型补对应章节，**必含三要素：作用（解决什么痛点）/ 原理（怎么实现）/ 用法（用户怎么操作）**：
- 新功能 → 在「§5.x 平台功能」加一节（仿 §5.5 题库上传），并在 §6 FAQ 加 1-2 条常见问法
- 新记忆系统 → §1 核心规模数字 +1、§3 记忆系统详解加一条（范式+特点+已知坑）、§4 时间线补一行
- 榜单/结论有变 → §1「当前榜单要点」更新
- 末尾「修订时间」追加日期 + 本次同步内容

### 2. 更新 DOC_WHITELIST（让助手能"查细节"）
`assistant_tools.py` 的 `DOC_WHITELIST` 加「语义名→docs 路径」：
- 新功能的契约/规范文档（如 `题库上传契约: docs/BANKS_API_CONTRACT.md`）
- 新系统的核实事实（如 `cognee核实事实: docs/COGNEE_VERIFIED_FACTS.md`）
- ⚠️ 排除历史执行 prompt 类文档（它们是指令文本，是注入风险源）

### 3. 部署 + 验证助手真的知道
- deploy.sh（KNOWLEDGE.md 在 backend/app/knowledge/ 随 tar 上线；docs/ 也随 tar，read_doc 可读）
- 公网问助手一句新功能相关问题，确认它答得出（不是「超出范围」）：
  `curl -u ... POST /api/assistant/chat` 或前端抽屉实测

## 自查（部署后）
```
公网 curl /api/assistant/info → knowledge_source.手册生成时间 是否更新
公网 curl /api/assistant/info → 可查文档 列表是否含新文档名
问助手新功能 → 答得出 = 同步成功
```
