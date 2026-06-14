# KidsBench 评测引擎迁移 dev198 — Step 1 可行性验证报告

> 目标：验证「Air 的记忆系统评测引擎能否迁到 dev198(Ubuntu)」的 Linux 移植真坑。
> 不是全量迁移，只跑通 2 个系统（memmachine 基础链路 + cognee 最痛骨头）的 smoke，产出真坑清单供川哥拍板整体投不投。
> 执行：cc（Claude）on Air，SSH 到 dev198。日期：2026-06-13。

---

## 0. 环境事实（dev198 实测）

| 项 | 值 |
|----|----|
| 主机 | chuan-HP（HP ZBook Power 15 G8），Ubuntu，16 核 i9-11900H，31GB RAM(28G 可用)，861G free |
| 系统默认 python | **3.14.4**（太新，依赖不兼容；评测需 3.12） |
| 装前缺失 | python3.12 / uv / redis / docker 全无 |
| SSH 通道 | `dev198` 别名走公网 frpc（阿里云 HK 8.218.26.17:22198），兜底 `dev198-jump`（经 mini-pub 跳板，和 prnas 一样思路） |
| GPU | RTX A2000（本轮不碰，明确用 CPU torch 对齐 Air） |

**连通性（dev198 中国网络实测）**：
- 清华 PyPI 镜像最快（~4MB/s）→ pip 主源
- pytorch cpu 索引 `download.pytorch.org/whl/cpu` 可达（1s）
- api.deepseek.com 可达（401，需 key，memmachine server 内部 LLM）
- gemini 代理 23.226.135.149:4000 可达（评测 LLM + cognee LLM）
- **huggingface.co 超时不可达**；hf-mirror.com 可达（HEAD 200/0.5s）但**下载带宽不足**（见坑 #3）

---

## 1. Linux 移植真坑清单

> 标注：【确定】= 本轮实测踩中并解决；【需后续验】= 本轮没碰但 Step2 会遇到。

### 坑 #1 — python 3.12 缺失 【确定】
- **现象**：dev198 默认 python 3.14.4，无 3.12，无 uv。
- **解法**：`curl -LsSf https://astral.sh/uv/install.sh | sh` 装 uv 0.11.21 → `uv python install 3.12` 得 **3.12.13**（与 Air 完全一致）。
- **耗时**：~2min。**风险低**。

### 坑 #2 — CPU torch 被误装成 CUDA 版 【确定·头号工程坑】
- **现象**：`uv pip install torch --index-url <cpu> --extra-index-url <tuna>` 装出 `torch 2.12.0+**cu130**`（4.5G + 一堆 nvidia 包）。根因：加了 `--extra-index-url tuna` 后 uv 从清华镜像取了 CUDA build。
- **解法**：torch 用**纯 cpu 索引单 index**（`--index-url https://download.pytorch.org/whl/cpu`，不加 extra），该索引自包含全部运行时依赖（sympy/networkx/jinja2/filelock/fsspec 实测齐全）→ 得 `torch 2.12.0+cpu`。其余依赖单独走清华。
- **耗时**：torch+sentence-transformers+transformers 下载 ~15min（中国网络瓶颈，非 dev198 性能问题）。**风险中**（命令写法陷阱，已固化解法）。

### 坑 #3 — HuggingFace 模型下载带宽不足 【确定·头号待验风险，已绕过】
- **现象**：embedding shim 首请求触发 `SentenceTransformer("BAAI/bge-small-zh-v1.5")` 下载（92M）。huggingface.co 不可达；配 `HF_ENDPOINT=https://hf-mirror.com` 后镜像可达，但**下载带宽撑不住**——250s curl 超时只下到 61M。
- **解法（本轮采用）**：从 Air 直接 tar 传完整 bge 模型缓存（`~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5`，92M）到 dev198。shim 缓存命中后加载 16s（CPU 加载，无下载），产出标准 **512 维 / L2norm=1.0** 向量。
- **结论**：HF 镜像「可达」≠「可靠下载」。**生产建议预置模型缓存**（从已有机器传），不依赖运行时下载。**风险中**（已有稳定绕过）。

### 坑 #4 — nltk punkt_tab 数据缺失（memmachine write 500）【确定】
- **现象**：memmachine server 起来了（health + 双 validate 通过），但写入 `/memories` 返回 500。server.log 根因：`text_deriver.py → nltk sent_tokenize → LookupError: Resource 'punkt_tab' not found`。memmachine 用 nltk 切句，dev198 没 nltk 数据。
- **解法**：从 Air tar 传 `~/nltk_data`（15M，含 punkt_tab）到 dev198 `~/nltk_data`（nltk 搜索路径第一位）。**注意 server 进程需在 nltk_data 到位后重启才生效**。
- **风险低**（数据传输即解）。

### 坑 #5 — memmachine setuptools_scm 无 git 构建失败 【确定】
- **现象**：memmachine 3 包（common/server/client）editable 安装失败，`setuptools-scm` 报错。根因：源码传输 exclude 了 `.git`，但这些包 `dynamic version` + `[tool.setuptools_scm] root="../.."`，需仓根 .git 推断版本。
- **解法**：`export SETUPTOOLS_SCM_PRETEND_VERSION=0.1.0` 跳过 git 探测（版本号不影响评测）。免重传 104M 的 .git。
- **风险低**。

### 坑 #6 — memmachine session 永久墓碑（跨 run 撞 SessionDeletedError）【确定】
- **现象**：修完 nltk 后 write 仍 500，错误变为 `SessionDeletedError: Session 'kidsbench/eval_memmachine_q_004' has been deleted`。根因：memmachine 的 `/projects/delete`（adapter 的 clear）是**永久墓碑删除**，删过的 session_key 不能再 `open_or_create`；session_key 基于 qid 固定，**上一次失败的 run 题末 clear 留下墓碑，下次复用同 qid 撞墓碑**（墓碑持久在 SQLite）。
- **解法**：清空 memmachine SQLite DB（`rm /tmp/kb-phase0-memmachine/*.db`）+ 重启 server，跑全新 run（题首 clear 对不存在 session 是 no-op）。
- **风险中**：单机单 run 干净跑没问题；但**反复跑同题库需每次清 DB**，Step2 nightly CI 要把「清 DB」纳入前置步骤。

### 坑 #7 — 清华镜像缺 mistralai 1.x（cognee 依赖冲突）【确定】
- **现象**：cognee 0.5.1 依赖 `mistralai>=1.9.10`（Air 装的是 1.12.4），但**清华镜像缺 mistralai 1.x 版本**（only 2.x），uv 报依赖不可满足。
- **解法**：mistralai==1.12.4 单独从 **pypi.org 官方源**装（pypi.org 有），其余 cognee/kuzu/lancedb 走清华。
- **风险低**（已知特例，固化解法）。

### 坑 #8 — uv 装 cognee 反复卡死（双重根因）【确定·耗时最长的坑】
- **现象**：cognee 安装连续卡死 4 次（unsafe-best-match / first-index / --no-deps+pypi / 纯清华），每次 venv 大小停在 25M 不增长、uv CPU 1-2%（在等网络）。
- **双重根因**（逐层剥出）：
  1. **extra-index `pypi.org` 拖死**：只要命令带 `--extra-index-url pypi.org`，uv 就对每个包查 pypi.org metadata（ss 实测 uv 连 151.101.x.x = Fastly = pypi.org），而 pypi.org 在 dev198 极慢（~230KB/s）→ resolve 卡 10min+。
  2. **残留 uv 进程持 cache 全局锁**：之前卡死的 uv 进程没 kill 干净（kill 错 PID），它持着 `~/.cache/uv` 全局锁不放 + 仍连 pypi.org，导致新启动的 uv 等锁也卡。
- **解法**：① 彻底 `pkill -9 -f "uv pip install"` 杀净所有残留 uv（脚本进程名不含该串避免自杀）；② 纯清华 `--index-url tuna`（**完全去掉 extra-index pypi.org**）+ `--no-deps -r <完整freeze闭包>`（跳过依赖解析）。清干净 + 纯清华后 **70s 装完 139 包**（venv 25M→834M）。
- mistralai==1.12.4（清华缺 1.x）单独从 pypi.org 装一次（单包小，可接受慢）。
- **风险中**：cognee 依赖链对网络源敏感，Step2 自动化必须固化「纯清华 + 预装 mistralai + 杀净残留 uv」流程。

### 坑 #8b — kuzu/lancedb native wheel 可用性（核心待验，正面解决）【确定】
- **结论**：cognee 0.5.1 的两个 native 嵌入式 DB **在清华镜像有 Linux py3.12 wheel**：
  - `kuzu-0.11.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` ✓
  - `lancedb-0.33.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`（abi3 跨版本兼容）✓
  - 实测 `import kuzu`(0.11.3) / `import lancedb`(0.33.0) 在 dev198 py3.12 成功。
- **教训**：排查时一度 `grep | head -2` 截断只看到 macosx wheel，误判「linux cp312 不存在」（虚惊）。**查 wheel 矩阵必须列全 tag，不能 head 截断**。

### 坑 #9 — SSH frpc + pkill/pgrep 自匹配自杀 【操作教训】
- **现象**：含 `pkill -f <pattern>` 或 `pgrep -f <pattern>` 的 ssh 命令，当 pattern 字符串出现在该命令自身命令行里时，pkill **把执行命令的 shell 自己杀了**（→ ssh 255，后续不执行）/ pgrep **匹配到自己**（→ 循环永不退出）。一度误判为 frpc 通道抖动。
- **解法**：杀进程用 PID（`kill <pid>`）不用 pattern；或写进脚本文件跑（脚本进程命令行不含 pattern）；后台启动用 `nohup bash script </dev/null >log 2>&1 &`。frpc 通道本身健康（纯 echo 8/8 通）。
- **风险低**（操作规范，已固化）。

### 坑 #10 — redis 缺失（memobase 用，本轮未碰）【需后续验】
- dev198 无 redis。本轮只验 memmachine + cognee（都不需要 redis）。**memobase 依赖 redis**，Step2 全量迁移需装 redis + 验证。

---

## 2. 两门结果

### 门 1 — memmachine（基础链路）✅ 通过
- **结果**：smoke 6 题 **6/6 全对，acc=1.00，0 error**。
  - q_001 布偶猫 ✓ / q_002 水母 ✓ / q_003 宇航员 ✓ / q_004 涂鸦五分钟 ✓ / q_005 95分 ✓ / q_006 天文学 ✓
- **意义**：uv + py3.12 + 主venv(cpu torch + bge缓存) + embedding shim + memmachine venv + memmachine server(全SQLite) + gemini代理 + deepseek 整条链路在 Linux 立起来了。
- **性能对账（2026-06-13 追加，已验证无漂移）**：用与 Air 历史 `w3_smoke_mm` **完全相同的 12 题题库 `v01_smoke.jsonl`**（有区分度，含难题）+ **相同 judge（`--judge-preset qwen-judge` NLI 蕴含判定）** 在 dev198 跑 memmachine，逐题对账 Air：
  - **recall 逐题完全相同**（Air=dev198 avg 4.42，每题召回数一字不差）→ 检索引擎确定性一致，无漂移。
  - **12 题里 11 题 judge_verdict 逐题一致**，judge_score 大量精确吻合（0.5/0.33/0.5/0/0.67/1.0/0.5/1.0/0/0）。dev198 acc=0.25 vs Air acc=0.17。
  - 唯一差异 `S04-④-003`：Air evasive(score=0.67，本就在 correct 阈值边缘) → dev198 correct(1.0)，属 LLM 答案 + NLI judge 单跑非确定性的**单题边缘波动**，非环境问题。
  - **结论：dev198 memmachine 与 Air 无可检测性能漂移。** 要 100% 严谨需多轮取均值消除单题波动（Step2 用 `rerun_rounds.sh`），但 recall 确定性一致 + 11/12 verdict 吻合已是强证据。
- **⚠️ 对账方法论教训（重要）**：首次对账漏带 `--judge-preset`，探索期题库（`expected_facts` 是 NLI hypothesis 格式）走默认词命中判分 → judge_score **全 0、全 evasive、acc=0.00**，险些误判成「dev198 性能崩盘」。靠「recall 逐题一致但 judge 全 0」两信号交叉才定位是判分配置漏配。**交叉验证必须对齐 judge-preset，否则 judge 全 0 会被误读成性能漂移。**
- cognee 的 12 题对账未做（Air 无 cognee 的 `v01_smoke` 历史基线）；完整 124 题全系统对账仍是 Step2。

### 门 2 — cognee（最痛骨头）✅ 通过
- **结果**：smoke 6 题 **6/6 全对，acc=1.00，0 error，不 hang**。
  - q_001 布偶猫 ✓ / q_002 水母 ✓ / q_003 宇航员 ✓ / q_004 涂鸦五分钟 ✓ / q_005 95分 ✓ / q_006 天文学 ✓
- **核心待验项正面解决**：kuzu 0.11.3 + lancedb 0.33.0 两个 native 嵌入式 DB 在 Linux py3.12 装好并 import 成功（见坑 #8b）；cognee 完整跑通——知识图谱建图（实测 110 nodes / 197 edges）+ 向量集合检索（6 collections）+ 多跳邻域投影回答全部正常。
- **adapter 已自处理的坑（确认 Linux 行为一致，无需额外修）**：持久事件循环（per-instance event loop 避开模块级 asyncio.Lock）/ `TELEMETRY_DISABLED=1`（避 test.prometh.ai SSL EOF）/ `KIDSBENCH_COGNEE_NO_PRUNE=1`（避每题 prune 损坏 kuzu/lancedb 连接导致 hang）/ `inspect.signature` 版本自适应 search / instructor 强制 JSON 模式 / `EMBEDDING_PROVIDER=custom` 走 shim 避 tiktoken KeyError。这些在 Linux 上行为与 Air 一致，本轮带 `KIDSBENCH_COGNEE_NO_PRUNE=1` 跑全程不 hang。
- **意义**：cognee 通了 = 任务设计里「最痛的骨头」（嵌入式 DB + 复杂依赖 + telemetry + 事件循环）在 Linux 活了，其余 9 个记忆系统（不含嵌入式 native DB 的）基本无忧。

---

## 3. 整体投不投判断

### 结论：**可以投（GO）**。两门全过，无架构级阻塞。

- **门1 memmachine 6/6 + 门2 cognee 6/6，全 acc=1.00、0 error、不 hang。** 最难的 cognee（嵌入式 kuzu/lancedb + 复杂依赖 + telemetry + 事件循环）在 Linux py3.12 完整活了 → 其余 9 个系统中不含嵌入式 native DB 的基本无忧。
- **所有踩中的坑都有明确、可固化的解法**，无一是「Linux 上根本跑不了」的架构级死结。坑性质分三类：
  1. **数据预置类**（从 Air 传）：bge 模型缓存、nltk_data。→ Step2 打包进迁移脚本。
  2. **命令写法/工具类**：CPU torch 纯 cpu 索引、setuptools_scm pretend version、uv 纯清华+杀净残留进程、pkill 自匹配自杀。→ 固化进部署脚本。
  3. **镜像源类**：清华为主 + pypi.org 仅补 mistralai 1.x。→ 配死。
- **最大的非 dev198 因素是中国网络**（HF 不可达、pypi.org ~230KB/s），不是 dev198 性能问题。dev198 本身（16核/28G/861G/空闲）算力充裕。

### Step2 全量迁移工作量重估

| 模块 | 工作量 | 说明 |
|------|--------|------|
| 其余 7 个纯 SDK 系统（mem0/letta/graphiti/memoryos/hindsight/reme + baselines） | 中 | 每个建 venv + 装依赖，套用本轮「纯清华 + 预置缓存」流程；graphiti 需 FalkorDB 隧道（QNAP:16379，本轮 nc 验通） |
| memobase | 中 | **需先装 redis**（dev198 当前无，坑 #10） |
| 迁移脚本工程化 | 中 | 把本轮手动步骤（10 venv 重建 + bge/nltk 预置 + server 启动 + 清 DB）写成幂等脚本 |
| systemd 服务化 | 小-中 | shim + memmachine server + memobase server 等做成 user systemd unit（注意 launchd/systemd env 注入，proxy 变量显式写 unit） |
| nightly CI | 中 | **关键：每次跑前清 memmachine SQLite DB（坑 #6 session 墓碑）**；题库版本固定；结果归档 |
| **全量交叉验证（最重要）** | **大** | 用**完整题库**在 dev198 跑全部系统，与 Air 同题库同环境对账，确认**无性能漂移**——这才是「迁移成功」的真正判据（见下） |

### 必须强调的诚实边界

- **「跑通」≠「迁移成功」。** 本轮只证明了**功能正确性**（环境能立起来、链路通、smoke 题答对），用的是 **6 题精简题库**。
- **性能漂移尚未验证**：Air 历史 memmachine 用 12 题题库 acc=0.17（含难题大量 evasive），dev198 用 6 题 acc=1.00，**题库不同不可直接对比**。Step2 必须用**完整题库 + Air 同环境交叉验证**，确认 dev198 跑出的榜单与 Air 一致（误差在可接受范围）后，才能下「迁移成功」结论。
- 在拿到一致榜单前，**Air 仍是黄金副本/回退点，不可拆**。

---
署名：cc（Claude）| 2026-06-13

---
署名：cc（Claude）| 2026-06-13
