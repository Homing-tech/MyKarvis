#!/usr/bin/env bash
# ============================================================
# Karvis 新网关一体化部署脚本（在腾讯云服务器上以 sudo 运行）
# 做五件事：备份旧项目 → 停掉旧 Karvis → 验证 9000 释放 → 部署新网关 → 自检
# 用法：sudo bash deploy/server_setup.sh
# ============================================================
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="/opt/karvis"
PORT=9000
TS="$(date +%Y%m%d_%H%M%S)"
BK_DIR="/root/karvis_backup"

log()  { echo -e "\n\033[1;36m==> $*\033[0m"; }
ok()   { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!!]\033[0m $*"; }

[ "$(id -u)" -eq 0 ] || { warn "请用 sudo 运行：sudo bash $0"; exit 1; }

# ------------------------------------------------------------
log "0/6 前置检查"
command -v docker >/dev/null || { warn "未安装 docker"; exit 1; }
df -h / | tail -1
FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
[ "$FREE_GB" -ge 3 ] || { warn "根分区仅剩 ${FREE_GB}G，先清理磁盘（docker image prune -a）"; exit 1; }

# ------------------------------------------------------------
log "1/6 备份旧项目与数据（包括旧 .env 里的 Key）"
mkdir -p "$BK_DIR"
FOUND_ANY=0
for d in /opt/karvis /root/my-karvis /root/karvis /home/ubuntu/my-karvis /home/ubuntu/karvis; do
  if [ -e "$d" ]; then
    FOUND_ANY=1
    tar -czf "$BK_DIR/karvis_old_${TS}$(echo "$d" | tr '/' '_').tar.gz" "$d" 2>/dev/null \
      && ok "已备份 $d"
  fi
done
[ "$FOUND_ANY" = 1 ] || warn "未找到常见旧项目目录，稍后按端口占用定位"
grep -h "DEEPSEEK" /opt/karvis/.env /root/my-karvis/.env /root/karvis/.env 2>/dev/null | sed 's/=sk-.*/=sk-***(已备份到 '"$BK_DIR"'）/' || true
ok "备份目录：$BK_DIR"

# ------------------------------------------------------------
log "2/6 停掉旧 Karvis（自动识别运行方式）"

# --- 2a. systemd
if systemctl list-unit-files 2>/dev/null | grep -qi "^karvis"; then
  systemctl stop karvis 2>/dev/null || true
  systemctl disable karvis 2>/dev/null || true
  ok "已停止并禁用 systemd 服务 karvis"
fi

# --- 2b. docker 容器（名字含 karvis 或映射了 9000）
MAP_IDS=$(docker ps -aq --format '{{.ID}} {{.Names}} {{.Ports}}' 2>/dev/null \
  | awk -v p=":${PORT}" 'index($0,"karvis") || index($2,p) || index($3,p) {print $1}')
if [ -n "${MAP_IDS// /}" ]; then
  for id in $MAP_IDS; do
    echo "    停止容器 $id ($(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's|/||'))"
    docker stop "$id" >/dev/null 2>&1 || true
    docker rm "$id" >/dev/null 2>&1 || true
  done
  ok "旧容器已停止并移除（数据卷未动，可随时回滚）"
fi

# --- 2c. 裸进程 / nohup / screen
if command -v ss >/dev/null && ss -lntp 2>/dev/null | grep -q ":${PORT} "; then
  PID=$(ss -lntp | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  if [ -n "${PID:-}" ]; then
    warn "端口 ${PORT} 仍被 PID $PID 占用：$(ps -p "$PID" -o cmd= 2>/dev/null)"
    kill "$PID" 2>/dev/null || true; sleep 2
    if ss -lntp 2>/dev/null | grep -q ":${PORT} "; then kill -9 "$PID" 2>/dev/null || true; sleep 1; fi
    ok "已终止进程 $PID"
  fi
fi

# ------------------------------------------------------------
log "3/6 验证端口 ${PORT} 已释放"
if ss -lntp 2>/dev/null | grep -q ":${PORT} "; then
  warn "端口 ${PORT} 仍被占用，暂停部署。请人工确认："
  ss -lntp | grep ":${PORT} " || true
  exit 1
fi
ok "端口 ${PORT} 已释放"

# ------------------------------------------------------------
log "4/6 安装新网关到 ${APP_DIR}"
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
# 注意：.env 永远不参与同步删除，否则 git 模式（仓库里没有 .env）会把服务器凭证删掉
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git' --exclude '__pycache__' --exclude 'data/' --exclude 'logs/' --exclude '.env' \
    "$SRC_DIR"/ "$APP_DIR"/
  [ -f "$APP_DIR/.env" ] || cp "$SRC_DIR/.env" "$APP_DIR/.env" 2>/dev/null || true
else
  warn "服务器没有 rsync，用 cp 代替（不删除旧文件，安全）"
  cp -a "$SRC_DIR"/. "$APP_DIR"/
  rm -rf "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/.git"
  find "$APP_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
fi
[ -f "$APP_DIR/.env" ] || { warn "$APP_DIR/.env 不存在（git 模式下需手工创建，见手册）"; }
grep -q "^DEEPSEEK_API_KEY=sk-" "$APP_DIR/.env" 2>/dev/null && ok "DeepSeek Key 已配置" || warn ".env 里 DeepSeek Key 为空"
cd "$APP_DIR"

# ------------------------------------------------------------
log "5/6 构建并启动（国内拉取 Docker Hub 失败时自动走镜像源）"
docker image inspect python:3.11-slim >/dev/null 2>&1 \
  || docker pull python:3.11-slim \
  || { docker pull docker.m.daocloud.io/library/python:3.11-slim \
       && docker tag docker.m.daocloud.io/library/python:3.11-slim python:3.11-slim; }

if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
$DC up -d --build
sleep 4
$DC ps

# ------------------------------------------------------------
log "6/6 自检"
curl -fsS "http://127.0.0.1:${PORT}/health" && echo && ok "健康检查通过" \
  || { warn "健康检查失败，看日志：cd $APP_DIR && $DC logs -f"; exit 1; }
docker exec karvis python tools/selftest.py 2>&1 | tail -20 || true

PUBLIC_IP=$(curl -fsS -m 5 https://meta.tencentyun.com/latest/meta-data/public-ipv4 2>/dev/null || echo "<你的公网IP>")
cat <<EOF

============================================================
✅ 部署完成。还差三步（企微后台 + 腾讯云控制台，必须手动）

1) 腾讯云控制台 → 轻量应用服务器 → 防火墙 → 添加规则：
   放行 TCP ${PORT}，来源 0.0.0.0/0

2) 企业微信后台 → 我的企业 → 企业信息 → 「企业可信 IP」
   填：${PUBLIC_IP}

3) 企微后台 → 每个应用 → 接收消息 → 设置 API 接收：
   阿龙管家   URL: http://${PUBLIC_IP}:${PORT}/wecom/life
   埼玉教练   URL: http://${PUBLIC_IP}:${PORT}/wecom/fitness
   阿尼亚督导 URL: http://${PUBLIC_IP}:${PORT}/wecom/mood
   Token / AESKey 与 .env 中对应 WECOM_*_TOKEN / WECOM_*_AESKEY 一致

验证：在企微应用里发一句话；看日志：cd ${APP_DIR} && $DC logs -f karvis
远程模拟回调：python tools/mock_callback.py life "你好" http://${PUBLIC_IP}:${PORT}
回滚旧版（如需）：备份在 ${BK_DIR}/，解压后按原方式启动即可
============================================================
EOF
