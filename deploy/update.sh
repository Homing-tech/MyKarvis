#!/usr/bin/env bash
# ============================================================
# 日常更新：拉最新代码 → 重建 → 自检（不删数据、不动 .env）
# 用法：sudo bash /opt/karvis/deploy/update.sh
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/karvis}"
PORT=9000
BRANCH="${BRANCH:-main}"

log() { echo -e "\n\033[1;36m==> $*\033[0m"; }
ok()  { echo -e "\033[1;32m[OK]\033[0m $*"; }

cd "$APP_DIR" || { echo "目录不存在：$APP_DIR"; exit 1; }

log "1/4 拉取最新代码"
git fetch --all --prune
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
git log --oneline -1

log "2/4 重建容器"
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
if [ "$1" = "--rebuild" ] || git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -qE 'requirements.txt|Dockerfile'; then
  $DC up -d --build
else
  $DC up -d --build     # 代码是 volume 挂载/COPY 进镜像，统一重建最稳
fi
sleep 3

log "3/4 自检"
curl -fsS "http://127.0.0.1:${PORT}/health" && echo
$DC ps

log "4/4 最近日志"
$DC logs --tail 15 karvis
ok "更新完成"
