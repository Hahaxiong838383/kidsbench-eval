# 题库上传 + CLI 桥 API 契约（前后端唯一依据）

> 2026-06-13。子问题 A（上传+转换+预览）+ CLI 命令桥。后端 cc / 前端 grok。
> 存储：`/app/data/banks/`（已挂的可写 ./data 卷，不碰 runs-mount:ro）。

## 数据模型

题库版本不可变：`v_<YYYYMMDD>_<sha8>`（sha8=CSV 内容 SHA256 前 8）。永不覆盖。
每版本目录 `/app/data/banks/<version>/`：`source.csv` / `questions.jsonl` / `issues.csv` / `meta.json`。

meta.json：
```json
{"version":"v_20260613_a1b2c3d4","created_at":"ISO8601","question_count":140,
 "issues_count":9,"task_type_dist":{"T1_recall":75,"T2_consistency":68,...},
 "csv_sha256":"...","original_filename":"xxx.csv","status":"validated"}
```

## API（前缀 /api/banks）

### POST /api/banks/upload  （multipart/form-data，字段 file=CSV）
转换在容器内**同步**跑（秒级）。校验：≤5MB、≤3000 行、content-type csv/text、UTF-8(BOM 容忍)。
- 200:
```json
{"version":"v_20260613_a1b2c3d4","question_count":140,"issues_count":9,
 "task_type_dist":{"T1_recall":75,...},
 "issues":[{"qid":"S04-④-003","kind":"missing_gold","detail":"..."}],  // 前 200 条
 "health":{"total_rows":149,"healthy":140,"dropped_redline":0,"skipped":9},
 "created_at":"...","original_filename":"xxx.csv"}
```
- 400 格式/校验失败（含 CSV 注入/超限），413 文件过大，422 转换零健康题。

### GET /api/banks  → 版本列表
`{"banks":[{version,created_at,question_count,issues_count,status,original_filename}...]}` 按 created_at 倒序。

### GET /api/banks/{version}  → 单版本详情（同 upload 返回结构，从 meta+issues.csv 读）
404 不存在。version 必须匹配 `^v_\d{8}_[0-9a-f]{8}$`（防路径穿越）。

### GET /api/banks/{version}/cli?adapters=cognee,memmachine  → 生成 Air CLI 命令
- 200: `{"command":"...","note":"...","est":{"adapters":2,"questions":140,"minutes_rough":"~4-8h","warn":"cognee 单跑 3-4h"}}`
- command 是可复制多行 bash：先 curl 下载该 bank jsonl 到本地 → 起依赖 server 提示 → run_eval。
  按 adapter 自动注入 env（cognee 必带 KIDSBENCH_COGNEE_NO_PRUNE=1 TELEMETRY_DISABLED=1，
  对应 venv .venv-<adapter>，server 依赖提示 setup 脚本）。adapters 白名单校验。

### GET /api/banks/{version}/download/{kind}  kind∈{questions,issues,source}
返回文件（jsonl/csv），Content-Disposition attachment。

## 前端 Banks.tsx（路由 /banks，nav 标签「题库上传」插在「题库」后）

1. 拖拽/选择 CSV 上传 → POST upload → 展示：
   - 健康摘要卡片：总行 / 健康 / 问题数（健康绿、问题橙）
   - 题型分布：task_type 横向条形（T1_recall/T2_consistency/...，标注哪些题型「缺席」=0）
   - issues 表格：qid / kind / detail（前 200，可下载完整 issues.csv）
2. 版本列表（GET /api/banks）：表格，点版本→详情
3. **CLI 命令桥**：adapter 多选框（cognee/memmachine/memobase/mem0/memoryos/graphiti/letta/hindsight/reme/基线）
   → GET cli → 展示命令 + est 估算 + warn 红字 + 一键复制按钮
4. 下载按钮：questions.jsonl / issues.csv
- API 走 fetch（同 api.ts 模式）；上传用 FormData。样式跟现有 Tailwind 风格。

## 安全/不可比红线（codex 审重点）
- CSV 注入：issues.csv 输出单元格 `=+-@\t\r` 前缀加 `'` 转义（人会用 Excel 打开）
- version 正则白校验防路径穿越；upload 文件名不入路径（只存 meta）
- 上传题库**永不进总榜**——只在「题库上传」页可见，与 v01 历史 runs 解耦
- 不可变：同 sha8 重复上传→返回已有版本不重写
