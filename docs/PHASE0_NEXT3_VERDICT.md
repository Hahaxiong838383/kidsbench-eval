# 第二波接入 Phase 0 裁决（go/no-go 一页纸）

> 2026-06-12 ｜ team 模式：3×Explore 源码扫描 + cc 主线全部实测验收 + 致命结论 cc 亲手复核
> 评审链：三发评审（grok-4.3/gpt-5.5/gemini-3.1-pro）定名单 → HippoRAG 2 中文硬伤复裁换 Vestige
> → Vestige Phase 0 一票否决 → 替补 Cognee 转正过 Phase 0。
> 四份核实文档：MEMOBASE / MEMMACHINE / COGNEE / VESTIGE _VERIFIED_FACTS.md

## 裁决

| 系统 | 裁决 | 一票否决门（LLM 注入/中文/清场） | 关键定位 | 主要黄旗 |
|---|---|---|---|---|
| **Memobase** | ✅ **GO** | 🟢🟢🟢（中文原生最优，零 patch） | 画像中心范式，K12 长期画像格子；自带 education 画像模板 | 停更 5 个月（依赖实测零腐烂）；**溯源仅 date-level**（画像是 LLM merge 派生物，同日多 turn 不能唯一绑定——codex 对抗审 P0 采纳，能力矩阵如实标 declared-weak，与 reflect 模式召回口径脚注同等处理）；event 检索必须接 shim |
| **MemMachine** | ✅ **GO** | 🟢🟢🟢 | 真值保存（原文+句级索引），T3/抗幻觉对照组 | 非幂等（写前查重）；token 计量弱 |
| **Cognee** | ✅ **GO（带 2 项 declared 能力缺口）** | 🟢🟢🟢（中文经 custom_prompt 100% 修复，A/B 实测） | 多跳联想（k-hop 邻域投影），与 graphiti 范式内对照 | instructor TOOLS quirk 需 monkey patch（钉版本 venv 缓解，碎了易察觉——抽取直接全炸不是静默错）；**虚拟时钟无注入口 + 溯源最弱，进能力矩阵 declared 列，Attribution/时序题会如实失分**（codex 对抗审 P0，采纳为显式声明而非掩盖） |
| ~~Vestige~~ | 🔴 **NO-GO** | —（FSRS 时钟 61 处墙钟硬编码，虚拟衰减链物理建不起来） | 「拟人化遗忘」格子继续空缺，留复评触发条件 | fork 级手术违反硬门槛 |
| ~~HippoRAG 2~~ | 🔴 NO-GO（上轮已裁） | —（text_processing ASCII 正则毁中文节点身份，5 处核心路径） | 多跳格子由 Cognee 顶上 | 9 个月停更 |

## 实测硬证据摘要（全部本机真跑，脚本可重跑）

- **Memobase**：中文画像「兴趣爱好/宠物: 养了一只布偶猫，名叫团子…[提及于2026-06-05]」（注入 T-7d 时间戳落地）；flush(sync=True) 14.5s 同步；重复写入画像不翻倍（LLM merge 兜住）；buffer 阈值 8192 配置生效
- **MemMachine**：全 SQLite 零外部服务起跑（最大疑虑解除）；turn_id metadata + score 原样回传；重启后 LTM 向量独立召回中文语义命中；project 物理删
- **Cognee**：中文实体 53%→**100%**（custom_prompt A/B）；2-hop 中文问答答对「宠物店」；prune 物理删实锤
- **Vestige**：cc 亲手 grep 复核 61 处 Utc::now() + IngestInput 无 created_at + review() 重盖 last_review

## Phase 1/2 工作量预估

| 系统 | Phase 1（基建固化） | Phase 2（adapter+契约测试） | 备注 |
|---|---|---|---|
| Memobase | 0.5 天（setup 脚本仿 letta） | 1 天 | 读取范式新（画像≠检索），read 设计要细 |
| MemMachine | 0.5 天 | 1 天 | API 最贴契约，最顺 |
| Cognee | 0.5 天（钉版本+patch 收编） | 1-1.5 天 | cognify 慢，149 题时序预算先估 |
| 合计 | ~1.5 天 | ~3.5 天 | 三家并行可压缩；之后 smoke→全量→上平台按 NEW_SYSTEM_CHECKLIST |

> ⚠️ 估时校正（codex 对抗审 P2 采纳）：上表是顺利路径；算上清场深度审计（orphan vector/Redis 残留）、
> 幂等重试场景契约测试、STM∪LTM 并集语义验证、149 题耗时预算实测，**全程按 5-8 人天排期**，不按 5 天内交付承诺。

## codex 对抗审采纳记录（2026-06-12）

- ✅ P0：Cognee 虚拟时钟/溯源 → 从「黄旗」升级为「verdict 表内显式 declared 缺口声明」
- ✅ P0：Memobase 溯源 → 降级为 date-level declared-weak（不能唯一绑定 turn）
- ✅ P0：MemMachine STM∪LTM 并集运行中语义（排序/去重/冲突）→ 列入 Phase 2 契约测试必做
- ✅ P1：清场深度审计（vector/Redis/billing 残留）+ 幂等并发重试场景 → Phase 2 契约测试必做
- ✅ P2：估时上调至 5-8 人天；Vestige 增补 libfaketime/时钟隔离 spike 选项（实验席，不占正式名额）
- ❌ 不采纳「token 计量不达标即不能 GO」：榜单「未上报」标注是平台既有机制（六家中四家同等待遇），成本可比性以 Pareto 曲线带价格表口径处理

## 范式覆盖地图增量（接入后）

画像沉淀（Memobase）/ 真值保存（MemMachine）/ 多跳邻域投影（Cognee）三格新开；
「拟人化遗忘」格子诚实空缺（Vestige 复评触发条件已登记）。
