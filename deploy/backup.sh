#!/usr/bin/env bash
# 服务器：数据备份（本地快照 + 可选上传腾讯云 COS）
#
# 用法：
#   cd /opt/karvis && sudo bash deploy/backup.sh              # 立即备份一次
#   cd /opt/karvis && sudo bash deploy/backup.sh --install-cron  # 装每日 03:00 自动备份
#
# 备份内容：data/（SQLite + 每日 md）+ assistants/（人格文案）
# 不备份：.env（含密钥，禁止出服务器）、logs/、backups/ 自身
#
# COS 配置（写入 /opt/karvis/.env，四项齐全才上传）：
#   COS_BUCKET=xxx-1234567890
#   COS_REGION=ap-guangzhou
#   COS_SECRET_ID=AKIDxxxx
#   COS_SECRET_KEY=xxxx
# 工具未装时执行：
#   wget -q https://cosbrowser.cloud.tencent.com/software/coscli/coscli-linux -O /usr/local/bin/coscli && chmod +x /usr/local/bin/coscli
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/karvis}"
cd "$APP_DIR" || exit 1
KEEP_DAYS="${KEEP_DAYS:-7}"

if [ "${1:-}" = "--install-cron" ]; then
  CRON="0 3 * * * cd $APP_DIR && bash $APP_DIR/deploy/backup.sh >> $APP_DIR/logs/backup.log 2>&1"
  ( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh'; echo "$CRON" ) | crontab -
  echo "已安装每日 03:00 自动备份，查看：crontab -l"
  exit 0
fi

TS="$(date +%Y%m%d-%H%M)"
NAME="karvis-data-$TS.tar.gz"
mkdir -p "$APP_DIR/backups" "$APP_DIR/logs"

# ① 本地快照
tar -czf "$APP_DIR/backups/$NAME" data assistants 2>/dev/null
if [ $? -ne 0 ]; then echo "打包失败：确认 $APP_DIR/data 与 assistants 存在"; exit 1; fi
SIZE=$(du -h "$APP_DIR/backups/$NAME" | cut -f1)
echo "快照完成：$APP_DIR/backups/$NAME（$SIZE）"

# ② 清理过期快照
find "$APP_DIR/backups" -name 'karvis-data-*.tar.gz' -mtime "+$KEEP_DAYS" -delete 2>/dev/null
echo "已清理 $KEEP_DAYS 天前的本地快照"

# ③ 上传 COS（四项配置齐全才执行）
get_env() { grep -E "^$1=" "$APP_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r'; }
COS_BUCKET="$(get_env COS_BUCKET)"
COS_REGION="$(get_env COS_REGION)"
COS_SECRET_ID="$(get_env COS_SECRET_ID)"
COS_SECRET_KEY="$(get_env COS_SECRET_KEY)"

if [ -z "$COS_BUCKET" ] || [ -z "$COS_REGION" ] || [ -z "$COS_SECRET_ID" ] || [ -z "$COS_SECRET_KEY" ]; then
  echo "COS 未配置（跳过上传）：在 $APP_DIR/.env 补齐 COS_BUCKET/COS_REGION/COS_SECRET_ID/COS_SECRET_KEY"
  exit 0
fi

if ! command -v coscli >/dev/null 2>&1; then
  echo "未装 coscli（跳过上传），安装命令："
  echo "  wget -q https://cosbrowser.cloud.tencent.com/software/coscli/coscli-linux -O /usr/local/bin/coscli && chmod +x /usr/local/bin/coscli"
  exit 0
fi

coscli config set --secret-id "$COS_SECRET_ID" --secret-key "$COS_SECRET_KEY" --bucket "$COS_BUCKET" --region "$COS_REGION" >/dev/null 2>&1
if coscli cp "$APP_DIR/backups/$NAME" "cos://$COS_BUCKET/karvis/$NAME" >/dev/null 2>&1; then
  echo "已上传到 COS：cos://$COS_BUCKET/karvis/$NAME"
else
  echo "COS 上传失败，本地快照仍在：$APP_DIR/backups/$NAME"
fi
