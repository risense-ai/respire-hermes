#!/usr/bin/env bash
# respire-hermes 插件打包 —— CI 与本地共用同一口径。
# 规范：zip 根=插件根（解压后根即 __init__.py/plugin.yaml，直接作 $HERMES_HOME/plugins/respire 内容）。
# 产出 dist/respire-hermes-<version>-<yyyymmdd>.zip，zip 注释行为构建基线 commit。
# stdout 恰两行：第一行 zip 路径，第二行 sha256。
# 兼容 macOS bash 3.2：变量展开一律 ${VAR} 花括号隔离。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VER="$(python3 - <<'PY'
import re
raw = open("plugin.yaml", encoding="utf-8").read()
m = re.search(r"^version:\s*(\S+)\s*$", raw, re.M)
print(m.group(1) if m else "")
PY
)"
test -n "${VER}"
REV="$(git rev-parse HEAD 2>/dev/null || echo nobaseline)"
STAMP="$(date +%Y%m%d)"
OUT="dist/respire-hermes-${VER}-${STAMP}.zip"

mkdir -p dist
rm -f "$OUT"

zip -r "$OUT" \
  __init__.py plugin.yaml cli.py README.md CHANGELOG.md LICENSE \
  .gitignore respire.config.example.json assets skills scripts tests .github \
  -x '*__pycache__*' '*.pyc' '*.DS_Store' \
  -x 'respire/plugin.log' >/dev/null

printf '%s\n' "$REV" | zip -z "$OUT" >/dev/null

echo "$OUT"
python3 -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" "$OUT"
