#!/usr/bin/env bash
# 本地检查入口（仓库暂无远端 CI）：隔离的后端测试、应用导入、前端 Lint 与构建、Git 禁入项扫描。
# 用法：scripts/check.sh [--skip-frontend]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
SKIP_FRONTEND=0

for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    *) printf '错误：未知参数 %s\n' "$arg" >&2; exit 2 ;;
  esac
done

say() {
  printf '\n== %s\n' "$*"
}

fail() {
  printf '检查失败：%s\n' "$*" >&2
  exit 1
}

[ -x "$BACKEND_DIR/.venv/bin/python" ] || fail "缺少 backend/.venv，请先按 README 安装后端依赖。"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sandrone_check.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

# 绝不连接真实数据库或拉起 TTS；微信与 TTS 只在应用 lifespan 中启动，测试不会触发。
export SANDRONE_DATABASE_URL="sqlite:///$TMP_DIR/check.db"
export SANDRONE_TTS_MODE=mock

say "后端测试（临时 SQLite + Mock TTS）"
(cd "$BACKEND_DIR" && .venv/bin/python -m unittest discover -s tests -t .)

say "后端应用导入"
(cd "$BACKEND_DIR" && .venv/bin/python -c "import app.main")

if [ "$SKIP_FRONTEND" -eq 0 ]; then
  say "前端 Lint、类型检查与构建"
  [ -d "$FRONTEND_DIR/node_modules" ] || fail "缺少 frontend/node_modules，请先运行 npm install。"
  (cd "$FRONTEND_DIR" && npm run lint --silent && npm run build --silent)
fi

say "Git 空白与禁入项"
cd "$ROOT_DIR"
git diff --check
git diff --cached --check
forbidden="$(git ls-files | grep -E '(^|/)data/|\.db$|\.db-(wal|shm)$|(^|/)\.env$|\.(pth|ckpt)$|(^|/)node_modules/|(^|/)\.venv/' || true)"
[ -z "$forbidden" ] || fail "以下文件不应进入 Git：
$forbidden"

say "全部通过"
