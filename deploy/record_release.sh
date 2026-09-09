#!/usr/bin/env bash
# 服务器：记录本次发布版本 + 重建容器（让 /health 返回新版本号）
#
# 用法： cd /opt/karvis && sudo bash deploy/record_release.sh v0.2.0
#
# 说明：它只负责「记录 + 生效」，代码拉取由 deploy/update.sh 完成。
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/karvis}"
cd "$APP_DIR" || exit 1

VER="${1:-}"
if [ -z "$VER" ]; then
  echo "用法: sudo bash deploy/record_release.sh v0.2.0"
  exit 1
fi
NUM="${VER#v}"

printf '%s\n' "$NUM" > "$APP_DIR/VERSION"

COMMIT="$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null)"
if [ -z "$COMMIT" ]; then COMMIT="unknown"; fi
export KARVIS_COMMIT="$COMMIT"

docker compose up -d --build
if [ $? -ne 0 ]; then echo "重建容器失败，看日志：docker logs karvis --tail 50"; exit 1; fi

mkdir -p "$APP_DIR/deploy"
echo "$(date '+%F %T')  $VER  commit=$COMMIT" >> "$APP_DIR/deploy/releases.log"

sleep 5
echo "—— 当前线上版本 ——"
curl -s "http://127.0.0.1:${PORT:-9000}/health"
echo ""
echo "发布记录已写入 $APP_DIR/deploy/releases.log"
