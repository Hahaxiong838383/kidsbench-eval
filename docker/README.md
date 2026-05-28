# KidsBench Docker 公共栈

## 用途

所有 Adapter 共享的基础服务（Vector DB + Graph DB + Cache），一次起，多 Adapter 复用。

## 端口表

| 服务 | 容器名 | 端口 | 用途 |
|---|---|---|---|
| Qdrant | `kidsbench-qdrant` | `6333` (HTTP) / `6334` (gRPC) | Vector DB（Mem0/Cognee/通用向量） |
| FalkorDB | `kidsbench-falkordb` | `6379` (Redis 协议) | Graph DB（Graphiti/Cognee KG） |
| Redis | `kidsbench-redis` | `6380` (映射 6379) | Cache / sidecar 持久化 / 限流计数 |

## 启停

```bash
# 起
docker compose -f docker/compose-base.yml up -d

# 看状态
docker compose -f docker/compose-base.yml ps

# 看日志
docker compose -f docker/compose-base.yml logs -f qdrant

# 停
docker compose -f docker/compose-base.yml down

# 停 + 清数据（小心，不可逆）
docker compose -f docker/compose-base.yml down -v
```

## 健康自检

```bash
# Qdrant
curl -s http://localhost:6333/healthz

# FalkorDB（用 redis-cli）
docker exec kidsbench-falkordb redis-cli ping

# Redis cache
docker exec kidsbench-redis redis-cli ping
```

## 部署在 QNAP

QNAP 当前已占端口：`22/80/139/445/2376/3000/3080/5000-5003/5444/8080/10052/58080`
本 compose 选的端口（6333/6334/6379/6380）均无冲突。

```bash
# QNAP 启停（注意 Container Station docker 路径）
DOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker

# 把整个仓库 rsync 到 QNAP（避开 .git / .venv）
rsync -av --exclude='.git/' --exclude='.venv/' --exclude='runs/' \
  /Users/rayman.chen/mycc/kidsbench-eval/ \
  prnas:/share/CACHEDEV2_DATA/kidsbench-eval/

# QNAP 起栈
ssh prnas "cd /share/CACHEDEV2_DATA/kidsbench-eval && \
  $DOCKER compose -f docker/compose-base.yml up -d"
```

## 资源占用（实测预估）

| 服务 | 内存 | 磁盘（数据增长） |
|---|---|---|
| Qdrant | 300-500 MB（100k 向量） | ~1 GB / 100k 向量 |
| FalkorDB | 200-400 MB | ~500 MB / 100k 节点 |
| Redis | 256 MB（cap） | < 100 MB |
| **合计** | **~1 GB** | **~1.5 GB** |

QNAP 剩余 12.1 GB 内存 + 7.1 TB 磁盘 → 完全够。
