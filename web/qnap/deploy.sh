#!/usr/bin/env bash
# KidsBench Web · B0 Day 2 部署到 QNAP
#
# 用法：
#   SSHPASS='xxx' bash web/qnap/deploy.sh
#
# 流程：
#   1. local build frontend dist
#   2. tar 打包 backend/ dist/ docker-compose.yml nginx.conf
#   3. scp 上 QNAP /share/Container/kidsbench-web/
#   4. ssh QNAP docker compose up -d --build
#   5. 验证 http://192.168.61.18:18081
#
# 安全：
#   - 用 SSHPASS env 不在 ps -ef 暴露密码
#   - 远端目录与现有 multica/falkordb 等独立（/share/Container/kidsbench-web/）
#   - 端口 18001/18081 避开所有现有占用
#   - 加入现有 kidsbench-net network，直连 falkordb/qdrant（service name）
#   - 限 mem/cpus 防资源争抢

set -e

PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
REMOTE_DIR="/share/Container/kidsbench-web"
TAR="/tmp/kidsbench-deploy.tgz"
QNAP_IP="192.168.61.18"
# 默认 prnas（经 mini 内网 ProxyJump）。mini 内网挂时可用 KIDSBENCH_HOST_ALIAS=prnas-pub 走公网
HOST_ALIAS="${KIDSBENCH_HOST_ALIAS:-prnas}"

if [ -z "$SSHPASS" ]; then
  echo "❌ SSHPASS env 未设。用法：SSHPASS='xxx' bash $0"
  exit 1
fi

step() { echo; echo "▶ [$1] $2"; }

step "1/6" "本地 build frontend dist"
(cd "$PROJECT/web/frontend" && npm run build 2>&1 | tail -3)

step "2/6" "打包 backend / frontend / configs（用 COPY_FILE_FLAG 防 macOS dot-underscore）"
rm -f "$TAR"
COPYFILE_DISABLE=1 tar czf "$TAR" \
  --exclude=".DS_Store" --exclude="._*" --exclude="__pycache__" \
  -C "$PROJECT/web/backend" requirements.txt app \
  -C "$PROJECT" src \
  -C "$PROJECT" configs \
  -C "$PROJECT/web/frontend" dist \
  -C "$PROJECT/web/qnap" Dockerfile.backend Dockerfile.frontend nginx.conf docker-compose.yml
ls -lh "$TAR"

step "3/6" "QNAP 准备目录 $REMOTE_DIR"
sshpass -e ssh "$HOST_ALIAS" "
  set -e
  mkdir -p $REMOTE_DIR/data $REMOTE_DIR/runs-mount $REMOTE_DIR/backend $REMOTE_DIR/frontend
"

step "4/6" "scp tar 到 QNAP"
sshpass -e scp "$TAR" "$HOST_ALIAS:$REMOTE_DIR/"

step "5/6a" "确保 base stack 容器加入 kidsbench-net（idempotent）"
sshpass -e ssh "$HOST_ALIAS" "
  DOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
  for c in kidsbench-falkordb kidsbench-qdrant kidsbench-redis; do
    \$DOCKER network connect kidsbench-net \$c 2>/dev/null && echo \"  ✓ \$c 加入 kidsbench-net\" || echo \"  • \$c 已在 kidsbench-net 或不存在\"
  done
"

step "5/6b" "QNAP 解压 + docker compose up"
sshpass -e ssh "$HOST_ALIAS" "
  set -e
  cd $REMOTE_DIR
  # 清理旧产物 + 解压
  rm -rf backend/app backend/requirements.txt frontend/dist frontend/nginx.conf
  tar xzf kidsbench-deploy.tgz
  # 后端：app/ + requirements.txt + src/ + configs/ + Dockerfile → backend/
  mv app backend/app
  mv requirements.txt backend/requirements.txt
  rm -rf backend/src backend/configs
  mv src backend/src
  mv configs backend/configs
  mv Dockerfile.backend backend/Dockerfile
  # 前端：dist/ + nginx.conf + Dockerfile.frontend → frontend/
  mv dist frontend/dist
  mv nginx.conf frontend/nginx.conf
  mv Dockerfile.frontend frontend/Dockerfile
  # 清理 macOS dot-underscore（双保险）
  find . -name '._*' -delete 2>/dev/null || true
  # 起 / 重建容器
  DOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
  # --force-recreate 防 docker 因 image tag 未变误认为 container 无需重启（B1 实战教训）
  \$DOCKER compose up -d --build --force-recreate 2>&1 | tail -20
  echo
  echo '--- container status ---'
  \$DOCKER compose ps
"

step "6/6" "内网验证"
sleep 5
echo "→ backend /healthz"
curl -s --max-time 5 "http://$QNAP_IP:18001/healthz" || echo "  ❌ backend 不通"
echo
echo "→ frontend /healthz (经 nginx 反代)"
curl -s --max-time 5 "http://$QNAP_IP:18081/healthz" || echo "  ❌ frontend 反代不通"
echo
echo "→ frontend / (HTML)"
curl -s --max-time 5 -o /dev/null -w "HTTP %{http_code} · %{size_download} bytes\n" "http://$QNAP_IP:18081/"

echo
echo "✅ 部署完成。内网访问："
echo "   http://$QNAP_IP:18081       (KidsBench Web 前端)"
echo "   http://$QNAP_IP:18001/docs  (FastAPI Swagger)"
echo
echo "公网入口待 HK nginx + DNS 配置完成"
