#!/usr/bin/env bash
# 在腾讯云轻量服务器上执行：把 karvis 跑起来
# 用法：bash deploy/install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/karvis}"
PORT="${PORT:-9000}"

echo "==> 目标目录：${APP_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  echo "!! 未检测到 docker，请先安装："
  echo "   curl -fsSL https://get.docker.com | bash && systemctl enable --now docker"
  exit 1
fi

mkdir -p "${APP_DIR}/data" "${APP_DIR}/logs"

if [ ! -f "${APP_DIR}/.env" ]; then
  echo "!! 缺少 ${APP_DIR}/.env"
  echo "   请把本地 karvis/.env 上传：scp karvis/.env root@<IP>:${APP_DIR}/.env"
  exit 1
fi
chmod 600 "${APP_DIR}/.env"

cd "${APP_DIR}"
echo "==> 构建并启动"
docker compose up -d --build

sleep 3
echo "==> 服务状态"
docker compose ps

echo "==> 本机自检"
if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null; then
  echo "[OK] 健康检查通过"
else
  echo "[FAIL] 健康检查失败，看日志：docker compose logs -f"
  exit 1
fi

cat <<EOF

==> 下一步（必须手动完成，否则能收消息但不回复）

1. 腾讯云控制台 → 防火墙 → 放行 TCP ${PORT}
2. 企业微信后台 → 我的企业 → 企业信息 → 底部「企业可信 IP」填本机公网 IP
3. 每个应用 → 接收消息 → 设置 API 接收：
   URL:      http://<公网IP>:${PORT}/wecom/life      （埼玉教练用 /wecom/fitness，阿尼亚督导用 /wecom/mood）
   Token:    填 .env 里该应用对应的 WECOM_<NAME>_TOKEN
   AESKey:   填 .env 里该应用对应的 WECOM_<NAME>_AESKEY
4. 应用里发一句话验证；无回复看日志：docker compose logs -f karvis
EOF
