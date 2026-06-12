# KidsBench AI 助手接入方案（V2.1，经 team 三方对抗评审修订 + 川哥需求更新）

> 状态：提案，待川哥拍板。2026-06-12。
> 评审：grok-4.3（推理）+ gpt-5.5（严谨）+ gemini-3.1-pro（学术）三发对抗，
> 原始 finding 见 /tmp/r-{grok,gpt,gem}.md（本文档已合成共识）。
> V2.1 变更（川哥 2026-06-12 指示）：① LLM 改走 codex 网关（不用 OpenAI 官方
> key）；② 新增管理后台 + 手机号授权使用。详见文末「V2.1 增补」。

## 目标

web 平台上加一个 AI 助手：互动解答平台相关问题（为什么这样设计、榜单怎么读、
判分逻辑、怎么接新系统），进一步能"解决问题"（诊断某次评测为什么失败）。
助手的记忆 = 本项目的构建思路和过程。LLM 用川哥的 OpenAI API key。

## 核心架构决策（评审驱动的三处修订）

### 修订 1：砍掉逐块向量 RAG（三家一致否决 V1 草案的 RAG 设计）

**为什么砍**（三家共识）：
- 语料才 ~238KB（30 个 docs），不到需要向量检索的体量；
- 300-500 字切块会打碎 markdown 表格/代码块/逻辑链——而"构建思路"恰恰是
  宏观连贯叙事，碎片召回答不了设计哲学类问题；
- 切块脚本 + 索引 + 文档改了 KB 不跟（stale KB）是纯增维护成本。

**改成什么**：「精选项目手册 + 按需取原文档」两层：
1. **KNOWLEDGE.md 项目手册**（~20-40k token，脚本生成）：设计哲学、核心概念
   （协议 v1.1 / 补丁层 / 判分三态 / 三基线）、7 系统 verified facts 精华、
   构建时间线（git log 整理）、踩坑教训。作为**字节级稳定的 system prompt 前缀**
   ——吃 OpenAI prompt caching，重复请求输入成本降 50-75%。
2. **read_doc(name) 白名单工具**：助手要细节时按需取完整原文档（全保真，
   不经切块）。白名单硬编码，**排除历史执行 prompt 类文档**（QUESTIONBANK_
   EXECUTION_PROMPT 等本身是指令文本，是文档内 prompt injection 的风险源）。

> 为什么不全量注入（gemini 的方案）：30 个 docs 里有过程性 prompt 文档和
> 过期 spec，原样全塞既浪费 token 又引入指令污染；精选手册 + 钻取工具
> 同时满足"稳定前缀利于缓存"（gpt）与"保留连贯性"（gemini）。

### 修订 2：后端无状态（gemini finding，采纳）

会话历史由前端持有（React 状态），每次请求全量带 messages 数组。后端不存
会话——容器重启/多 worker 都不丢不串，消灭 TTL 管理。配合单请求总 token
预算（历史窗口超限在前端截断）。

### 修订 3：「解决问题」靠诊断工具层（gemini finding，采纳）

只给静态知识答不了"为什么 graphiti 第 42 题判错"。tools 增加只读诊断层：
- `get_run_log(run_id, qid)` — 读 runs/ 下该题的完整事务记录（prompt/回答/判分）
- `get_leaderboard()` / `get_findings()` / `get_question(qid)` / `get_adapter_info(name)`
全部只读、参数 schema 严格校验、文件路径 resolve 后必须在白名单目录内
（防 path traversal，gemini P1）、单轮 tool call 次数上限。

## 安全与成本（P0 项，上线门槛）

| # | 风险（评审来源） | 对策 |
|---|---|---|
| 1 | 公网单共享账号 + 私人 key = 成本炸弹（3/3 P0）| 三级限流：nginx per-IP limit_req → 单请求 token 预算 → 全局每日用量熔断（原子文件计数）。OpenAI 侧设 project budget cap 兜底。usage 日志 + 界面展示当日余量 |
| 2 | QNAP 出网 api.openai.com 未验证（3/3 P0）| `OPENAI_BASE_URL` 可配（官方/中转/代理）；deploy.sh 加 connectivity preflight，不通则阻塞部署 |
| 3 | key 注入链路（grok P0）| compose 加 `env_file` 指向 QNAP 宿主上 gitignored 的 env 文件（chmod 600），key 不进 git/不进前端/不进日志 |
| 4 | CSRF 触发消费（gpt P0）| 校验 Origin/Referer，拒绝无 Origin 的浏览器态 POST；CORS 保持白名单 |
| 5 | prompt injection（3/3）| 检索文档/工具结果统一包装为"资料非指令"；输入长度上限；引用 source_id 由后端校验存在性，不靠模型自觉（gpt P1）|
| 6 | SSE 被 nginx 60s 超时切断（grok P1，已核实 nginx.conf 仅 /api/run/*/stream 有长超时）| /api/assistant/chat 单独 location：长超时 + X-Accel-Buffering no；客户端断开即取消上游 OpenAI 请求 |
| 7 | 预训练知识污染（gemini P2，本平台特有）| mem0/letta 是知名开源项目，LLM 会用自己（可能过时的）预训练知识抢答。system prompt 强规则：仅基于手册与工具结果；平台未验证的特性必须声明"超出 KidsBench 验证范围" |

## 显性化（平台一贯纪律）

- `GET /api/assistant/info`：助手自我白盒说明——知识来源（手册哪些文档、
  生成时间）、工作机制（手册前缀 + 工具钻取）、能力边界（**只解答与诊断，
  不能改代码/跑实验**，grok P1 的边界声明）、知识截止时间。
- 回答内嵌来源引用（doc 路径 + 标题锚点），点击跳对应说明页。
- 前端：全局浮动按钮 + 侧边抽屉 chat，所有页面可呼出。

## 知识更新机制（grok P1）

`scripts/build_assistant_manual.py` 重新生成 KNOWLEDGE.md（docs 变更后手动跑
或挂 git hook）；手册带生成时间戳，/info 透出，杜绝"看起来最新其实是旧的"。

## 明确不做（V1 边界）

- 不 dogfood 被测记忆系统（评测环境配置为跑分服务，耦合风险大）→ Phase 2 候选
- 不做跨会话个性化记忆（共享账号无用户身份）
- 不给任何写操作
- 不用 OpenAI Assistants API（grok 提议，否决：黑盒检索与平台白盒纪律冲突）

## 工作量预估

后端 assistant.py + 手册生成脚本 + nginx location + 限流 + 前端抽屉 ≈ 1.5-2 天。
依赖：openai SDK（或 httpx 直调）；不新增向量库、不新增容器。

## 待实施时必须验证的事实（no-guessing）

1. QNAP 容器 → 网关连通性（实测；国内 CF 网关，预期比 api.openai.com 乐观得多）
2. 网关2 每日配额恢复后补一轮对比测速（不阻塞开发，主备结构先行）

---

# V2.1 增补（川哥 2026-06-12 指示）

## A. LLM 改走 codex 网关（替代 OpenAI 官方 key）

**实测（2026-06-12，Air 直连，gpt-5.5 + reasoning_effort=low + stream，2 轮）**：

| 网关 | TTFT | 总耗时（~300 token 答案） | 状态 |
|---|---|---|---|
| 网关1 `10521052.xyz` | 2.6-2.9s | 5.0-5.3s | ✅ 可用 |
| 网关2 `cc-sub2.whtaibang.top` | — | — | ❌ 当日 429 DAILY_LIMIT_EXCEEDED |

**结论**：默认网关1 主路 + 网关2 自动降级（429/5xx 时 failover，与 team
_codex_lane.sh 同套路）。网关2 测速待其配额恢复后补。

**三条实现铁律**（来自既有 memory，违反必踩坑）：
1. **必须流式**：两网关都在 Cloudflare 后，非流式重 reasoning 请求 ~100s 必 524。
   tool-calling 中间轮也走流式（解析 streamed tool_call delta）。
2. **网关2 WAF 拦 Python UA**：后端 httpx 必须自定义 User-Agent（不含 "python"）。
3. **直连不走代理**：国内网关，请求不经 xray。

**对原 P0 的影响**：
- P0-2（QNAP 出网）风险大降——国内 CF 网关替代 api.openai.com，部署时仍实测。
- P0-3（key 注入）机制不变，注入对象换成网关 key（env_file，不进 git）。
- **新增注意**：网关有每日总配额（网关2 已实证被当日其他用途耗尽），助手用量
  与川哥开发工具/出图**共享配额**——按手机号归因的用量日志因此更重要。
- prompt caching 经中转网关是否生效**未知**：稳定前缀设计保留（零成本），
  但成本测算不按官方缓存折扣算。

## B. 管理后台 + 手机号授权

**用户流**：使用者首次打开助手 → 输入手机号 → 后端查白名单 → 签发签名
token（TTL，localStorage）→ chat 请求携带 → 按手机号配额/限流/用量记录。

**管理后台**（/admin，密码登录，密码哈希存 QNAP env 文件不进 git）：
- 手机号白名单 CRUD（号码 + 姓名备注 + 启用开关 + 每日配额）
- 用量看板：按手机号 × 按日的请求数/token 数
- 全局设置：助手总开关、每日总预算熔断、模型/网关档位
- 登录接口限速（防爆破）；session 接口 per-IP 限速（防手机号枚举）

**存储**：SQLite（compose `./data:/app/data` 挂载已就位，容器重启不丢）。

**安全定位（诚实声明）**：只输手机号 = 身份**标识**而非**认证**——知道同事
手机号即可冒用。内部平台 + 外层 Basic Auth 双层下 V1 可接受；用量面板按号
可见使冒用可被发现。要堵死需 Phase 2 加短信验证码（需接短信服务）。
手机号属 PII：界面半遮蔽显示（138****1234），日志不落明文。

**对原 P0-1 的影响**：共享账号无法归因的问题被此需求直接解决——per-phone
配额取代 per-IP 限流成为主防线（per-IP 限速仍保留在 session/login 接口）。

## 工作量更新

原 1.5-2 天 + 管理后台/手机号会话 ≈ 1 天 → **总 2.5-3 天**。

## 待川哥提供

1. 管理后台密码（拿到后哈希入 QNAP env 文件）
2. 确认 V1 不做短信验证码（默认不做）

---

# V2.2 增补：双档模型路由（经 team 三发对抗评审修订，2026-06-12）

> 评审：grok-4.3 + gpt-5.5 + gemini-3.1-pro。原始 finding /tmp/r-{grok,gpt,gem}.md。
> cc 实测佐证：deepseek-v4-flash FC 三路通过（单轮选对工具/多轮需 reasoning_content
> 原样回传/无需工具不乱调）；Qwen3.6 FC 通过（2.85s）；项目已有 169 行自研
> httpx llm_client（src/kidsbench/middleware/llm_client.py）。

## 路由架构（评审修订版）

```
用户提问 → [激进规则路由] → 简单档：deepseek-v4-flash（纯手册问答 + read_doc，无诊断工具）
                          → 诊断/复杂档：默认引擎待金标集实测定（候选 Qwen3.6-thinking / 网关1 gpt-5.5）
                          → 手动升级档：网关1 gpt-5.5（二次确认 + per-phone 每日升级上限）
```

## 评审驱动的 6 处修订（vs 初版构思）

1. **砍掉独立 LLM 分类器**（gemini 砍/gpt 保守化/grok 限缩——三家合流）：
   误判诊断题让弱模型瞎答的代价 >> 多烧点配额。改为：规则激进化（含
   "为什么/诊断/失败/对比/题号/run ID"等语义一律上档）+ **fail-closed**
   （规则不确定 → 宁可上档）+ 手动升级。不再单独花一跳分类调用。
2. **简单档物理隔离诊断工具**（三家共识）：简单档 schema 只给 read_doc；
   诊断工具是复杂档专属。"这个问题需要诊断工具"本身成为最干净的路由规则——
   简单档模型被教育成"答不了就提示用户点深度诊断"。
3. **降级语义分级**（三家最大 P0 共识）：网关挂/配额尽时，事实问答无缝降
   国产；**诊断题明确拒答**（"诊断额度耗尽/网关离线，请稍后再试"）——
   降级弱模型硬答诊断题 = 权威胡说，比拒答恶劣，摧毁平台公信力。
4. **三档可选结构**（gemini 独家方案级建议）：Qwen3.6-35B-A3B 是 thinking
   模型且 SiliconFlow 成本极低不占网关配额，CoT 天然适合日志分析——可作
   中间「诊断档」，gpt-5.5 只留极难+手动升级。**FC 前提已实测成立**；
   多步工具编排可靠性未验证 → 实施时建 30-50 条金标集（手册问答/失败诊断/
   跨文档综合/诱导胡说），横测 Qwen 诊断档 vs gpt-5.5 low/medium effort，
   用数据定二档还是三档（gpt+grok 同样要求金标集验证 gpt-5.5 low 是否够）。

   > **横测裁决（2026-06-12 实施时完成，5 道失败诊断题）**：qwen 4/5 有效
   > 但 1/5 哑火（124s 仅吐 2 字），平均 44s；gpt-5.5 5/5 稳定平均 17s 但吃
   > 共享配额。→ **三档保留：诊断档 = qwen 主路 + 网关 gpt-5.5 弱答
   > （<20 字）自动兜底**，~80% 诊断流量零网关配额，哑火自动切强引擎
   > （用户可见切换提示）。实现见 assistant_llm.py chat_stream。
5. **LiteLLM 否决，扩展现有 llm_client**（gemini 明确+grok 倾向+事实佐证）：
   所有上游全是 OpenAI 兼容格式，LiteLLM 解决的 schema 转换问题不存在；
   且 CF 流式/WAF 自定义 UA 这些怪行为反而要穿透它的抽象层。扩展项目
   现有 169 行 httpx client 加流式+FC，零新依赖。
6. **配额-路由耦合**（grok 独家 P0）：路由层注入该手机号剩余配额——
   配额紧张时复杂档收紧，管理后台可见"今日强模型余量"。

## 交互细节（评审采纳项）

- 手动升级：二次确认（"将消耗强模型配额"）+ per-phone 每日升级上限 +
  日志打标 user_forced（未来路由调优数据）+ 升级后**覆盖**弱模型旧答案
  （gemini：保持对话历史干净线性，避免新旧答案逻辑冲突污染上下文）
- 模型角标显示「简单档/诊断档」档位名而非裸模型名（grok：对非技术用户更有意义）
- prompt 缓存纪律（gemini）：静态手册放 messages 最前且字节级不变，
  动态变量（日期/榜单状态）一律放静态文本之后，否则缓存全失效
- 前端组件定 **assistant-ui**（headless、只需实现 onSendMessage，契合后端
  无状态设计；CopilotKit 要接管状态管理+自带 runtime，侵入性过强）

## 评审未采纳项（诚实记录）

- gpt 提出工具参数级 ACL（per-phone 资源裁剪）：**部分采纳**——评测数据
  本来就是平台内全员可见（Basic Auth 后无分级），不做参数级 ACL；
  保留 per-phone 审计日志。
- grok 提出 deepseek FC "可靠性<95% 则禁工具"：已被修订 2 的物理隔离
  覆盖（简单档只剩 read_doc 单工具，面窄风险小），50 轮实测降为实施时
  smoke 项而非阻断项。

## 实现注意（cc 实测发现的协议坑）

- deepseek-v4-flash 是 thinking 模型：多轮工具流必须把 assistant 消息的
  `reasoning_content` 原样回传，否则 400（"must be passed back"）。
- 流式 tool_call delta 解析两档都要做（网关 CF 后必须流式）。
