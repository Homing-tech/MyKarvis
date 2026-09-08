#!/usr/bin/env bash
# ============================================================
# 首次部署：从 Git 仓库克隆到 /opt/karvis 并启动
# 用法：sudo bash deploy/setup_from_git.sh <仓库地址> [分支]
#   例：sudo bash deploy/setup_from_git.sh https://gitee.com/xxx/karvis.git
#   例：sudo bash deploy/setup_from_git.sh git@github.com:xxx/karvis.git main
# 前提：/opt/karvis/.env 已存在（用 deploy/create_env.sh 生成，或手工创建）
# ============================================================
set -euo pipefail

REPO="${1:-}"
BRANCH="${2:-main}"
APP_DIR="/opt/karvis"
PORT=9000
TS="$(date +%Y%m%d_%H%M%S)"
BK_DIR="/root/karvis_backup"

log()  { echo -e "\n\033[1;36m==> $*\033[0m"; }
ok()   { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!!]\033[0m $*"; }

[ "$(id -u)" -eq 0 ] || { warn "请用 sudo 运行"; exit 1; }
[ -n "$REPO" ] || { warn "缺少仓库地址，用法：sudo bash $0 <git地址> [分支]"; exit 1; }

# ------------------------------------------------------------
log "1/5 备份旧 Karvis"
mkdir -p "$BK_DIR"
for d in /opt/karvis /root/my-karvis /home/ubuntu/my-karvis; do
  [ -e "$d" ] && tar -czf "$BK_DIR/karvis_old_${TS}$(echo "$d" | tr '/' '_').tar.gz" "$d" 2>/dev/null \
    && ok "已备份 $d"
done

# ------------------------------------------------------------
log "2/5 停掉占用 ${PORT} 的旧服务"
systemctl list-unit-files 2>/dev/null | grep -qi "^karvis" && { systemctl stop karvis 2>/dev/null || true; systemctl disable karvis 2>/dev/null || true; ok "systemd karvis 已停"; } || true

for id in $(docker ps -aq --format '{{.ID}} {{.Names}} {{.Ports}}' 2>/dev/null \
  | awk -v p=":${PORT}" 'index($0,"karvis") || index($3,p) {print $1}'); do
  docker stop "$id" >/dev/null 2>&1 || true; docker rm "$id" >/dev/null 2>&1 || true
done
ok "旧容器已清理"

if ss -lntp 2>/dev/null | grep -q ":${PORT} "; then
  PID=$(ss -lntp | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  [ -n "${PID:-}" ] && { kill "$PID" 2>/dev/null || true; sleep 2; ss -lntp | grep -q ":${PORT} " && kill -9 "$PID" 2>/dev/null || true; ok "已终止进程 $PID"; }
fi
ss -lntp 2>/dev/null | grep -q ":${PORT} " && { warn "端口 ${PORT} 仍被占用，暂停"; ss -lntp | grep ":${PORT} "; exit 1; }
ok "端口 ${PORT} 已释放"

# ------------------------------------------------------------
log "3/5 拉取代码到 ${APP_DIR}"
command -v git >/dev/null || { warn "服务器没装 git：apt-get install -y git"; exit 1; }
mkdir -p "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  cd "$APP_DIR" && git fetch --all --prune && git checkout "$BRANCH" && git reset --hard "origin/$BRANCH"
else
  rm -rf "${APP_DIR:?}"/* 2>/dev/null || true
  git clone -b "$BRANCH" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR" && git log --oneline -1
ok "代码已就位"

# ------------------------------------------------------------
log "4/5 检查 .env（凭证不进 git，必须服务器本地创建）"
if [ ! -f "$APP_DIR/.env" ]; then
  warn "$APP_DIR/.env 不存在"
  cat <<EOF
  请在服务器上执行（内容见 deploy/create_env.sh 或 .env.example）：
    sudo bash $APP_DIR/deploy/create_env.sh      # 若该文件随包上传了
    # 或手工：sudo vi $APP_DIR/.env
EOF
  exit 1
fi
chmod 600 "$APP_DIR/.env"
grep -q "^DEEPSEEK_API_KEY=sk-" "$APP_DIR/.env" && ok "DeepSeek Key 已配置" || warn ".env 里 DeepSeek Key 为空"

# ------------------------------------------------------------
log "5/5 构建并启动"
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
docker image inspect python:3.11-slim >/dev/null 2>&1 \
  || docker pull python:3.11-slim \
  || { docker pull docker.m.daocloud.io/library/python:3.11-slim \
       && docker tag docker.m.daocloud.io/library/python:3.11-slim python:3.11-slim; }
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
$DC up -d --build
sleep 4; $DC ps
curl -fsS "http://127.0.0.1:${PORT}/health" && echo && ok "健康检查通过" || { warn "失败，看日志：$DC logs -f"; exit 1; }

PUBLIC_IP=$(curl -fsS -m 5 https://meta.tencentyun.com/latest/meta-data/public-ipv4 2>/dev/null || echo "<公网IP>")
cat <<EOF

============================================================
✅ 部署完成，还差三步手工配置：
1) 腾讯云控制台防火墙放行 TCP ${PORT}
2) 企微后台「企业可信 IP」填 ${PUBLIC_IP}
3) 三应用回调 URL：
   阿龙管家   http://${PUBLIC_IP}:${PORT}/wecom/life
   埼玉教练   http://${PUBLIC_IP}:${PORT}/wecom/fitness
   阿尼亚督导 http://${PUBLIC_IP}:${PORT}/wecom/mood
以后更新代码只需：sudo bash $APP_DIR/deploy/update.sh
============================================================
EOF
