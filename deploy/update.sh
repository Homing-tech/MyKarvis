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
warn(){ echo -e "\033[1;33m[!!]\033[0m $*"; }

cd "$APP_DIR" || { echo "目录不存在：$APP_DIR"; exit 1; }

log "1/4 拉取最新代码"
CUR_URL="$(git remote get-url origin 2>/dev/null || echo '')"
# 私有仓库兜底：镜像站转发不了凭证，只能带 token 直连
TOKEN_URL=""
if [ -n "${GITHUB_TOKEN:-}" ]; then
  case "$CUR_URL" in https://github.com/*) TOKEN_URL="https://${GITHUB_TOKEN}@${CUR_URL#https://}";; esac
fi
FETCHED=0
for u in "$CUR_URL" "https://ghfast.top/${CUR_URL}" "https://gh-proxy.com/${CUR_URL}" "https://ghproxy.net/${CUR_URL}" "$TOKEN_URL"; do
  if [ -z "$u" ] || [ "$u" = "https://ghfast.top/" ]; then continue; fi
  echo "  尝试：$u"
  if timeout 90 git fetch --depth=1 "$u" "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}" 2>/dev/null; then
    ok "拉取成功 ← $u"; FETCHED=1
    if [ "$u" != "$CUR_URL" ]; then git remote set-url origin "$u"; fi
    break
  fi
done
[ "$FETCHED" = "1" ] || { echo -e "\033[1;31m[!!] 所有源都拉不到，保留现有版本继续运行\033[0m"; exit 1; }
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
git log --oneline -1

log "2/4 重建容器"
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
if [ "${1:-}" = "--rebuild" ] || git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -qE 'requirements.txt|Dockerfile'; then
  $DC up -d --build
else
  $DC up -d --build     # 代码是 volume 挂载/COPY 进镜像，统一重建最稳
fi
sleep 3

log "3/4 自检"
if curl -fsS "http://127.0.0.1:${PORT}/health"; then echo; ok "健康检查通过"; else warn "健康检查未通过，看下面日志"; fi
$DC ps

log "4/4 最近日志"
$DC logs --tail 15 karvis
ok "更新完成"
