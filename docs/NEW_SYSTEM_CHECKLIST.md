# 新记忆系统接入·展示位同步清单

> 教训来源（2026-06-12）：接入 ReMe 时只更新了「评测榜单」和「Adapter 架构页」，
> 漏了「记忆系统页」「范式覆盖地图」「榜单一句话定位」3 个展示位——川哥两次
> 反馈「web 没看到 reme」。根因：展示位分散在 3 个文件 4 个静态字典 + 数据驱动若干，
> 零散更新必漏。本清单是接入新系统的强制同步项，做完逐条打勾。

## 一、代码层静态登记位（4 处，必须全改）

| # | 字典/常量 | 文件 | 作用（哪个页面）| reme 实证 |
|---|---|---|---|---|
| 1 | `ADAPTERS` | `web/backend/app/architecture.py` | **Adapter 架构页**（/adapters）：SDK/方法行号/known_issues 白盒 | ✅ |
| 2 | `MEMORY_SYSTEMS` | `web/backend/app/architecture.py` | **记忆系统页**（/memory）：tldr/机制/schema 人话介绍。key 命名 `<name>_storage` | ✅ |
| 3 | `ADAPTER_PLAIN` | `web/backend/app/qb_report.py` | **评测总榜**每行「一句话定位」+ MD 报告 | ✅ |
| 4 | `PARADIGM_HOME_GROUND` | `web/backend/app/questionbank.py` | **范式×题型覆盖地图**：主场题型 + 依据（防「垫底=没价值」误读）| ✅ |

## 二、数据驱动展示位（跑批+归档自动，无需改代码）

| # | 来源 | 触发 |
|---|---|---|
| 5 | 评测总榜数据行 | 跑全量 `--include-<sys>` → 同步 runs-mount → 自动出现 |
| 6 | 历史快照矩阵 | `save_snapshot()` 归档 → 同步 history/ → 自动出现 |

## 三、测试断言（改了静态登记位必同步）

- `web/backend/tests/test_main.py::test_architecture_overview` 的 adapters 集合（加新 key）
- `test_architecture_adapter_404` 用的「不存在 adapter」名（别撞新加的）

## 四、接入新系统的标准收尾流程（按序执行）

```
1. adapter + 契约 mock + harness 装配（Phase 2）
2. 【代码登记】4 处静态字典全补（ADAPTERS / MEMORY_SYSTEMS / ADAPTER_PLAIN / PARADIGM_HOME_GROUND）
3. 【测试】更新 test_main.py 断言 → 全量 pytest 绿
4. 【跑批】smoke 验收 → 全量 149 → 同步 runs-mount
5. 【归档】save_snapshot → 同步 history/
6. 【部署】deploy.sh → 公网验证：
   curl .../api/architecture            # adapters 含新系统？
   curl .../api/questionbank/leaderboard # 榜单含新系统？
   curl .../api/questionbank/paradigm-coverage # 范式地图含新系统？
7. 【自查】grep -c '"<新系统名>"' 上述 4 个文件，全 ≥1 才算齐
```

## 五、一键自查脚本

```bash
# 接入新系统后跑一遍，4 个登记位全 ✅ 才算同步完成
SYS=reme  # 改成新系统名
for loc in \
  "ADAPTERS:web/backend/app/architecture.py:\"$SYS\":" \
  "MEMORY_SYSTEMS:web/backend/app/architecture.py:${SYS}_storage" \
  "ADAPTER_PLAIN:web/backend/app/qb_report.py:\"$SYS\":" \
  "PARADIGM:web/backend/app/questionbank.py:\"$SYS\","; do
  name=${loc%%:*}; rest=${loc#*:}; file=${rest%%:*}; pat=${rest#*:}
  cnt=$(grep -c "$pat" "$file" 2>/dev/null)
  echo "$name: $([ "$cnt" -gt 0 ] && echo ✅ || echo ❌缺)"
done
```
