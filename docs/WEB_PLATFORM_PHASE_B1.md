# KidsBench Web Platform — B1 阶段实施计划

> 阶段：B1（Pipeline 时间线 + 实时事件流）
> 父 SPEC：[`WEB_PLATFORM_SPEC.md`](./WEB_PLATFORM_SPEC.md)
> 前置：[`WEB_PLATFORM_PHASE_B0.md`](./WEB_PLATFORM_PHASE_B0.md) ✅
> 工时：2d / 6 子阶段

---

## 1. B1 目标

**把"跑一题"从黑盒变白盒** — 看见 harness 在每一步做了什么：

1. 哪个 turn 在 write，调了几次 embedding，每次多少 ms
2. mem0 内部 LLM 抽了什么 facts，token 用了多少
3. memoryos 三层迁移什么时候触发
4. graphiti 抽实体 + 关系传给 LLM 的 prompt 是什么
5. search 的 top-k 召回结果是什么，分数多少
6. final answer LLM 看到什么 prompt，回什么
7. 评分判定怎么打的（keyword 命中哪几个 + semantic 多少）

**关键**：实时看（跑题中刷新就能看到 step）+ 历史可回放（点旧 run_id 重看时间线）

### 不在 B1 范围

- ❌ 题库 CRUD（B2 做）
- ❌ 触发跑题（B3 做，B1 只展示已跑的 + 后台跑的实时流）
- ❌ 横向 diff 召回结果（B2 做）
- ❌ 干预（改 query 重搜 → B3）

---

## 2. 架构（关键设计）

### 2.1 实时事件流双通道（合 Gemini A.1）

```
Air harness 跑题
    │
    ├─[实时热数据] 每个 hook → HTTP POST → QNAP /api/run/{id}/event
    │                                       ↓
    │                                  内存 asyncio.Queue (per run_id)
    │                                       ↓
    │                                  SSE 推送给前端 (Last-Event-ID 续传)
    │
    └─[冷数据归档] 写 /tmp/runs/<run_id>/pipeline.jsonl    ← 写临时目录
              ↓ 跑完 mv 到 ~/runs/                       ← 原子替换 (Gemini A.3)
              ↓ fswatch + rsync                        ← 持久化到 QNAP
              QNAP /share/Container/kidsbench-web/runs-mount/
```

### 2.2 数据格式（OTel-lite span 模型，Gemini C.1）

`pipeline.jsonl` 每行一个 span event：

```json
{"event_id":1,"span_id":"sp-1","parent_id":null,"name":"run_question","type":"ENTER","ts":1234567890.000,"qid":"q_001"}
{"event_id":2,"span_id":"sp-2","parent_id":"sp-1","name":"adapter.write","adapter":"mem0","type":"ENTER","ts":1234567890.100,"turn_text":"我家有只布偶猫叫团子"}
{"event_id":3,"span_id":"sp-3","parent_id":"sp-2","name":"embedding.encode","is_query":false,"text_preview":"我家有只...","embedding_b64":"AaBb...","dim":512,"type":"EXIT","ts":1234567890.150,"duration_ms":50}
{"event_id":4,"span_id":"sp-4","parent_id":"sp-2","name":"llm.call","provider":"gemini-3.5-flash","prompt_preview":"...","completion_preview":"...","prompt_tokens":120,"completion_tokens":45,"type":"EXIT","ts":1234567890.250,"duration_ms":100}
{"event_id":5,"span_id":"sp-2","name":"adapter.write","type":"EXIT","ts":1234567890.300,"duration_ms":200}
```

**关键字段**：
- `event_id` — 全局递增，Last-Event-ID 续传用
- `span_id` + `parent_id` — 嵌套树结构（前端 reactflow 用）
- `type` — ENTER / EXIT（成对）
- `embedding_b64` — base64 内嵌（Gemini C.2 消灭小文件）
- `*_preview` — 长文本截断 200 字符防爆

### 2.3 Instrumentation 7 hook 点

| 层 | hook 点 | 包装方式 | 代码位置 |
|---|---|---|---|
| L2 harness | `run_question` 进出 | `@span("run_question")` | `harness/run_eval.py` |
| L0.5 中间件 | `EmbeddingService.encode` | `@span("embedding.encode")` | `src/kidsbench/middleware/embedding.py` |
| L0.5 中间件 | LLM 调用 (openai SDK) | monkey-patch `openai.OpenAI` | 新建 `src/kidsbench/trace/llm_hook.py` |
| L0 adapter | `adapter.write/read/clear` | wrapper 类 `TracedAdapter` | `harness/run_eval.py` 包装 |
| L0 mem0 内部 | mem0 内部 LLM 调用 | 走 openai 全局 hook 自动捕获 | 同上 monkey-patch |
| L0 graphiti 内部 | graphiti 内部 LLM 调用 | 已有自定义 `KidsBenchGraphitiLLMClient` → 直接加 trace | `middleware/graphiti_compat.py` |
| L2 评分 | `judge` 进出 + 关键词/语义分数 | `@span("judge")` | `harness/run_eval.py` |

**核心**：Python `contextvars` 把 `run_id` + `current_span_id` 传到调用栈各层，trace.py 统一发 event。

---

## 3. 子阶段拆分

| 子阶段 | 内容 | 工时 |
|---|---|---|
| **B1.0** | trace 核心模块 + 单测（contextvars + HTTP POST + 本地 jsonl）| 0.3d |
| **B1.1** | harness 加 trace（7 hook 点）+ 跑通 1 题不破坏现有评测 | 0.5d |
| **B1.2** | 后端 SSE endpoint + asyncio.Queue + Last-Event-ID | 0.3d |
| **B1.3** | 前端 reactflow swimlane + memo + throttle 5Hz | 0.5d |
| **B1.4** | 历史 run 详情页（jsonl 回放）| 0.2d |
| **B1.5** | caffeinate 包 harness + 端到端公网验证 + 部署 | 0.2d |
| **合计** | | **2d** |

---

## 4. 关键文件清单

### 新建

| 文件 | 用途 |
|---|---|
| `src/kidsbench/trace/__init__.py` | trace 模块入口 |
| `src/kidsbench/trace/span.py` | `@span` 装饰器 + contextvars |
| `src/kidsbench/trace/exporter.py` | HTTP POST + 本地 jsonl 双通道 |
| `src/kidsbench/trace/llm_hook.py` | openai SDK 全局 hook (mem0/memoryos 内部 LLM) |
| `web/backend/app/events.py` | POST /api/run/{id}/event + asyncio.Queue |
| `web/backend/app/stream.py` | GET /api/run/{id}/stream (SSE) |
| `web/frontend/src/pages/RunDetail.tsx` | 单 run 详情页 + Pipeline timeline |
| `web/frontend/src/components/PipelineTimeline.tsx` | reactflow swimlane 组件 |
| `web/frontend/src/lib/sse.ts` | SSE client + Last-Event-ID 续传 |
| `harness/run_caffeinated.sh` | caffeinate -i 包装启动 |

### 修改

| 文件 | 改动 |
|---|---|
| `harness/run_eval.py` | 加 trace import + 装饰器 + TracedAdapter wrapper |
| `src/kidsbench/middleware/embedding.py` | 加 `@span("embedding.encode")` |
| `src/kidsbench/middleware/graphiti_compat.py` | `_RealGraphitiWrapper` LLMClient 加 trace |
| `web/backend/app/main.py` | 挂 events.py + stream.py 路由 |
| `web/frontend/src/App.tsx` | 加 `/runs/:group/:runId` 路由 → RunDetail |
| `web/frontend/package.json` | + `reactflow` + `lodash.throttle` |
| `web/qnap/docker-compose.yml` | backend volume `./runs-mount:/app/runs:rw`（B0 是 :ro）|

---

## 5. 关键技术决策

### 5.1 trace 模块的"零侵入"原则

- 不强制 adapter 接 trace。`@span` 装饰器**可选**，不装 trace 时函数照常跑
- trace exporter 失败**绝不抛错**到业务流程（HTTP POST 用 try/except + 后台线程）
- `run_id == None` 时所有 span 静默丢弃（dev/test 模式）

### 5.2 SSE 续传机制（Gemini B.2）

后端 `GET /api/run/{run_id}/stream?last_event_id=N`：
1. 从 jsonl 重放 N+1 之后的历史 events 一次性推
2. 然后挂在 asyncio.Queue 监听新 events
3. 每 15s 推一个 `: ping` 心跳防 nginx idle timeout（Gemini B.3）

前端：
```typescript
const sse = new EventSource(`/api/run/${runId}/stream?last_event_id=${lastId}`);
sse.onmessage = (e) => {
  setEvents(prev => [...prev, JSON.parse(e.data)]);
  lastId = e.lastEventId;
}
// 浏览器内置自动重连
```

### 5.3 Pipeline 时间线渲染（Gemini C.3 throttle）

```typescript
const throttledSetNodes = useMemo(
  () => throttle((events: SpanEvent[]) => {
    setNodes(eventsToNodes(events));
  }, 200),  // 5Hz
  []
);
useEffect(() => { throttledSetNodes(events); }, [events]);
```

reactflow 配置 `onlyRenderVisibleElements={true}` + 自定义节点 `React.memo`。

---

## 6. 验收标准

- ✅ 跑一题 `harness/run_eval.py q_001 --adapter mem0`，本地生成 `runs/<run_id>/pipeline.jsonl` 含 7 类 span events
- ✅ 同时 HTTP POST 到 QNAP backend，浏览器打开 `/runs/<group>/<run_id>` 实时看到 swimlane 滚动出现
- ✅ 断网重连后，SSE 从 last_event_id 续传不丢事件
- ✅ 单 run pipeline >30 节点时前端 FPS 不掉到 30 以下
- ✅ trace 装上不破坏现有评测：不带 trace 跑 smoke 6 题 vs 带 trace 跑，summary.json 一致
- ✅ Mac 合盖 → caffeinate 让 harness 不挂

---

## 7. 风险点

| 风险 | 概率 | 应对 |
|---|---|---|
| trace 装饰器嵌套深度太深拖慢 harness | 中 | 用 `time.monotonic()` 而非 `time.time()`；exporter 走后台线程不阻塞 |
| mem0/memoryos SDK 内部 LLM 调用 hook 不到 | 高 | monkey-patch `openai.OpenAI`（两家都用 openai SDK）|
| QNAP nginx SSE buffer 没关导致前端卡 5s | 低 | B0 已配 `proxy_buffering off` + `X-Accel-Buffering: no`（已验证）|
| Air 到 QNAP HTTP POST 跨网失败 | 低 | exporter 双通道：HTTP 失败时本地 jsonl 兜底 |
| reactflow 30+ 节点卡顿 | 中 | throttle 5Hz + memo + onlyRenderVisible |

---

## 8. 开工顺序（最小可验证 → 完整）

### Day 1 上午（B1.0 + B1.1 一部分）

1. 写 `trace/span.py` + `trace/exporter.py` + 单测
2. harness/run_eval.py 加 `@span("run_question")` 装饰器
3. 跑 1 题 verify `pipeline.jsonl` 正确落地
4. 不破坏现有评测（跑 smoke 6 题 summary.json 一致）

### Day 1 下午（B1.1 剩余 + B1.2）

1. 加 EmbeddingService / LLM client / adapter 各处 hook
2. 后端 events.py + stream.py
3. 本地 curl 测 SSE 推送

### Day 2 上午（B1.3）

1. 前端 PipelineTimeline 组件
2. 本地 dev server 看一次跑题的实时 swimlane

### Day 2 下午（B1.4 + B1.5）

1. 历史 run 详情页（已跑完的回放）
2. caffeinate 包装 + 端到端公网验证
3. 部署 + commit + push（跑 `/kidsbench-landing`）

---

## 9. 关联文档

- 父 SPEC：[`WEB_PLATFORM_SPEC.md`](./WEB_PLATFORM_SPEC.md)（§4 数据模型 + §8 Gemini finding 落地）
- 前置：[`WEB_PLATFORM_PHASE_B0.md`](./WEB_PLATFORM_PHASE_B0.md)
- Gemini 评审：`/tmp/gemini_kidsbench_web_review.md`（A.1/A.3/B.1/B.2/C.1/C.2/C.3 在 B1 落地）

---

**审阅人**：川哥
**实施人**：cc 主线
**预计开工**：拍方向后立刻进 B1.0
