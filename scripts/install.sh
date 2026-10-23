#!/usr/bin/env bash
# 安装 respire provider 到 $HERMES_HOME/plugins/respire/（目录插件官方位）。
# 复制排除 .git/dist/__pycache__/测试运行残留；重复执行=覆盖更新。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_HOME_DIR/plugins/respire"

mkdir -p "$DEST"
# macOS bash 3.2 兼容：tar 管道复制 + 排除
tar -C "$SRC" \
  --exclude './.git' \
  --exclude './dist' \
  --exclude './__pycache__' \
  --exclude './.pytest_cache' \
  --exclude './respire/plugin.log' \
  --exclude './.DS_Store' \
  -cf - . | tar -C "$DEST" -xf -

echo "installed → $DEST"
echo "next: hermes memory setup  (choose respire; leave rsrs path empty for auto-detect), then restart Hermes"
echo "verify:   hermes respire status"
