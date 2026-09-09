#!/usr/bin/env bash
# 发布新版本（本地执行）：更新 VERSION → 写 CHANGELOG → commit → 打 tag → 推送
#
# 用法：
#   bash deploy/release.sh patch  "修复 xxx 提示错误"
#   bash deploy/release.sh minor  "新增埼玉教练周报"
#   bash deploy/release.sh v1.0.0 "正式版"
#
# 只做本地（版本号 + 变更记录 + Git tag）。服务器更新用另外两条命令，脚本结尾会打印。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

BUMP="${1:-}"
MSG="${2:-}"

if [ -z "$BUMP" ] || [ -z "$MSG" ]; then
  echo "用法: bash deploy/release.sh <patch|minor|major|vX.Y.Z> \"一句话说明\""
  exit 1
fi

CUR="$(tr -d '[:space:]' < VERSION 2>/dev/null)"
if [ -z "$CUR" ]; then CUR="0.0.0"; fi

case "$BUMP" in
  patch|minor|major)
    IFS='.' read -r MA MI PA <<< "$CUR"
    if [ "$BUMP" = "major" ]; then MA=$((MA + 1)); MI=0; PA=0; fi
    if [ "$BUMP" = "minor" ]; then MI=$((MI + 1)); PA=0; fi
    if [ "$BUMP" = "patch" ]; then PA=$((PA + 1)); fi
    NEW="$MA.$MI.$PA"
    ;;
  v*) NEW="${BUMP#v}" ;;
  *)  NEW="$BUMP" ;;
esac

TAG="v$NEW"
echo "版本: $CUR -> $NEW (tag $TAG)"

# —— 安全检查：.env 绝不能进版本库 ——
if git diff --cached --name-only | grep -qx '.env'; then
  echo "错误：.env 已加入暂存区，先执行 git reset HEAD .env"
  exit 1
fi
if ! git check-ignore -q .env; then
  echo "警告：.env 未被 .gitignore 忽略，请确认后再发布"
  exit 1
fi

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "错误：tag $TAG 已存在。换版本号，或先 git tag -d $TAG"
  exit 1
fi

# —— 写 VERSION ——
printf '%s\n' "$NEW" > VERSION

# —— 写 CHANGELOG：插到 [Unreleased] 段落之后 ——
if [ ! -f CHANGELOG.md ]; then
  echo "错误：CHANGELOG.md 不存在"
  exit 1
fi
TODAY="$(date +%F)"
TMP="$(mktemp)"
awk -v ver="$NEW" -v d="$TODAY" -v msg="$MSG" '
  /^## \[Unreleased\]/ { print; inrel=1; next }
  inrel && /^---[[:space:]]*$/ {
    print; print ""; print "## [" ver "] - " d; print ""; print "- " msg; print ""; print "---"; inrel=0; next
  }
  { print }
' CHANGELOG.md > "$TMP" && mv "$TMP" CHANGELOG.md
echo "CHANGELOG 已更新"

# —— 提交与打 tag ——
git add -A
if git diff --cached --quiet; then
  echo "没有需要提交的改动，中止发布"
  exit 1
fi
git commit -m "release: $TAG - $MSG"
if [ $? -ne 0 ]; then echo "commit 失败"; exit 1; fi

git tag -a "$TAG" -m "$MSG"
if [ $? -ne 0 ]; then echo "打 tag 失败"; exit 1; fi

git push origin main
if [ $? -ne 0 ]; then echo "推送 main 失败"; exit 1; fi
git push origin "$TAG"
if [ $? -ne 0 ]; then echo "推送 tag 失败（代码已推，稍后可 git push origin $TAG 补推）"; exit 1; fi

echo ""
echo "本地发布完成：$TAG"
echo ""
echo "接下来到服务器执行（两行）："
echo "  cd /opt/karvis && sudo bash deploy/update.sh"
echo "  cd /opt/karvis && sudo bash deploy/record_release.sh $TAG"
