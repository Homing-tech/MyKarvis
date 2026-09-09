#!/usr/bin/env bash
# 服务器：把代码回滚到指定版本（数据不动，且回滚前自动快照）
#
# 用法： cd /opt/karvis && sudo bash deploy/rollback.sh v0.1.0
#        cd /opt/karvis && sudo bash deploy/rollback.sh --list     # 看历史发布记录
#
# 原理：从 GitHub 下载该 tag 的 tarball → 覆盖代码（排除 .env/data/backups）→ 重建容器。
# 数据可单独恢复：见 deploy/backup.sh 与 docs/项目管理规范.md 第 5 节。
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/karvis}"
REPO="${REPO:-Homing-tech/MyKarvis}"
cd "$APP_DIR" || exit 1

if [ "${1:-}" = "--list" ]; then
  if [ -f "$APP_DIR/deploy/releases.log" ]; then
    cat "$APP_DIR/deploy/releases.log"
  else
    echo "还没有发布记录（deploy/releases.log 不存在）"
  fi
  exit 0
fi

VER="${1:-}"
if [ -z "$VER" ]; then
  echo "用法: sudo bash deploy/rollback.sh v0.1.0"
  echo "      sudo bash deploy/rollback.sh --list"
  exit 1
fi
NUM="${VER#v}"

echo "即将回滚到 $VER（当前版本：$(cat "$APP_DIR/VERSION" 2>/dev/null || echo 未知)）"
echo "数据不会被改动，但会先做一次快照。"
read -r -p "确认回滚？输入 y 继续： " ANS
if [ "$ANS" != "y" ]; then echo "已取消"; exit 0; fi

# ① 回滚前快照（脚本不存在也不阻断）
if [ -f "$APP_DIR/deploy/backup.sh" ]; then
  bash "$APP_DIR/deploy/backup.sh" || echo "警告：快照失败，继续回滚"
fi

# ② 下载该版本 tarball（镜像站只转发 GET，tarball 是唯一可靠通道）
TMP="$(mktemp -d)"
OK=0
for MIRROR in "https://ghfast.top/https://github.com" "https://gh-proxy.com/https://github.com" "https://ghproxy.net/https://github.com" "https://github.com"; do
  URL="$MIRROR/$REPO/archive/refs/tags/$VER.tar.gz"
  echo "尝试下载：$URL"
  if curl -fsSL --max-time 90 "$URL" -o "$TMP/pkg.tar.gz"; then
    SZ=$(wc -c < "$TMP/pkg.tar.gz")
    echo "下载成功（${SZ} 字节）"
    if [ "$SZ" -gt 1000 ]; then OK=1; break; fi
  fi
  echo "该源失败，换下一个"
done

if [ "$OK" -ne 1 ]; then
  echo "所有源都下载失败。可手动下载后放到 $TMP/pkg.tar.gz，或检查 tag 是否存在"
  exit 1
fi

tar -xzf "$TMP/pkg.tar.gz" -C "$TMP"
SRC_DIR="$(find "$TMP" -maxdepth 1 -type d -name "${REPO##*/*}-*" | head -1)"
if [ -z "$SRC_DIR" ]; then
  echo "解包后找不到源码目录"
  exit 1
fi

# ③ 覆盖代码，保留 .env / data / backups / logs
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude '.env' --exclude 'data/' --exclude 'backups/' --exclude 'logs/' --exclude '.git/' "$SRC_DIR/" "$APP_DIR/"
else
  cp -a "$SRC_DIR/." "$APP_DIR/"
fi

printf '%s\n' "$NUM" > "$APP_DIR/VERSION"
echo "$(date '+%F %T')  ROLLBACK to $VER" >> "$APP_DIR/deploy/releases.log"

# ④ 重建并验证
cd "$APP_DIR" && docker compose up -d --build
sleep 6
echo "—— 回滚后状态 ——"
if curl -fsS "http://127.0.0.1:9000/health"; then
  echo ""
  echo "回滚完成，已回到 $VER"
else
  echo "健康检查未通过，看日志：docker logs karvis --tail 50"
  exit 1
fi
