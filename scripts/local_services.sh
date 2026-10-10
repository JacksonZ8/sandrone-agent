#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
RUNTIME_DIR="$ROOT_DIR/data/runtime"
BACKEND_SCRIPT="$ROOT_DIR/run_backend.sh"
FRONTEND_SCRIPT="$ROOT_DIR/run_frontend.sh"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_URL="http://127.0.0.1:8000/health"
FRONTEND_URL="http://127.0.0.1:5173/"
START_TIMEOUT="${SANDRONE_LOCAL_START_TIMEOUT:-30}"
STOP_TIMEOUT=12

STARTED_BACKEND=0
STARTED_FRONTEND=0

say() {
  printf '%s\n' "$*"
}

warn() {
  printf '警告：%s\n' "$*" >&2
}

fail() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}

service_label() {
  case "$1" in
    backend) printf '后端' ;;
    frontend) printf '前端' ;;
    *) return 1 ;;
  esac
}

service_pid_file() {
  printf '%s/%s.pid' "$RUNTIME_DIR" "$1"
}

service_log_file() {
  printf '%s/%s.log' "$RUNTIME_DIR" "$1"
}

service_script() {
  case "$1" in
    backend) printf '%s' "$BACKEND_SCRIPT" ;;
    frontend) printf '%s' "$FRONTEND_SCRIPT" ;;
    *) return 1 ;;
  esac
}

service_dir() {
  case "$1" in
    backend) printf '%s' "$BACKEND_DIR" ;;
    frontend) printf '%s' "$FRONTEND_DIR" ;;
    *) return 1 ;;
  esac
}

service_url() {
  case "$1" in
    backend) printf '%s' "$BACKEND_URL" ;;
    frontend) printf '%s' "$FRONTEND_URL" ;;
    *) return 1 ;;
  esac
}

service_port() {
  case "$1" in
    backend) printf '8000' ;;
    frontend) printf '5173' ;;
    *) return 1 ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少系统命令：$1"
}

validate_timeout() {
  case "$START_TIMEOUT" in
    ''|*[!0-9]*) fail "SANDRONE_LOCAL_START_TIMEOUT 必须是非负整数" ;;
  esac
}

process_birth() {
  ps -p "$1" -o lstart= 2>/dev/null | awk '{$1=$1; print}'
}

same_process() {
  local pid="$1"
  local expected_birth="$2"
  local current_birth

  kill -0 "$pid" 2>/dev/null || return 1
  current_birth="$(process_birth "$pid")"
  [ -n "$current_birth" ] && [ "$current_birth" = "$expected_birth" ]
}

process_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | awk '/^n/ {sub(/^n/, ""); print; exit}'
}

process_matches_service() {
  local service="$1"
  local pid="$2"
  local expected_dir command cwd

  kill -0 "$pid" 2>/dev/null || return 1
  expected_dir="$(service_dir "$service")"
  cwd="$(process_cwd "$pid")"
  [ "$cwd" = "$expected_dir" ] || return 1

  command="$(ps -p "$pid" -o command= 2>/dev/null)"
  [ -n "$command" ] || return 1
  case "$service" in
    backend)
      [[ "$command" == *"$BACKEND_SCRIPT"* ]] ||
        { [[ "$command" == *"uvicorn"* ]] && [[ "$command" == *"app.main:app"* ]]; }
      ;;
    frontend)
      [[ "$command" == *"$FRONTEND_SCRIPT"* ]] ||
        [[ "$command" == *"npm run dev"* ]] ||
        [[ "$command" == *"node"*"vite"* ]]
      ;;
    *)
      return 1
      ;;
  esac
}

owned_pid() {
  local service="$1"
  local pid_file pid expected_birth

  pid_file="$(service_pid_file "$service")"
  [ -f "$pid_file" ] || return 1

  IFS='|' read -r pid expected_birth < "$pid_file" || true
  case "$pid" in
    ''|*[!0-9]*)
      warn "$(service_label "$service") PID 文件无效，已清理"
      rm -f "$pid_file"
      return 1
      ;;
  esac

  if [ -z "${expected_birth:-}" ] ||
    ! same_process "$pid" "$expected_birth" ||
    ! process_matches_service "$service" "$pid"; then
    warn "$(service_label "$service") PID 已失效或不属于当前项目，已清理记录且未终止进程"
    rm -f "$pid_file"
    return 1
  fi

  printf '%s\n' "$pid"
}

write_pid_file() {
  local service="$1"
  local pid="$2"
  local pid_file birth temp_file

  birth="$(process_birth "$pid")"
  [ -n "$birth" ] || return 1
  pid_file="$(service_pid_file "$service")"
  temp_file="$pid_file.tmp.$$"
  printf '%s|%s\n' "$pid" "$birth" > "$temp_file"
  mv "$temp_file" "$pid_file"
}

port_in_use() {
  lsof -nP -iTCP:"$(service_port "$1")" -sTCP:LISTEN -t >/dev/null 2>&1
}

service_healthy() {
  curl -fsS --max-time 2 "$(service_url "$1")" >/dev/null 2>&1
}

wait_for_health() {
  local service="$1"
  local pid="$2"
  local pid_file state_pid expected_birth
  local elapsed=0

  pid_file="$(service_pid_file "$service")"
  [ -f "$pid_file" ] || return 1
  IFS='|' read -r state_pid expected_birth < "$pid_file" || true
  [ "$state_pid" = "$pid" ] && [ -n "${expected_birth:-}" ] || return 1

  while [ "$elapsed" -lt "$START_TIMEOUT" ]; do
    if service_healthy "$service"; then
      process_matches_service "$service" "$pid"
      return $?
    fi
    same_process "$pid" "$expected_birth" || return 1
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}

collect_process_tree() {
  local pid="$1"
  local birth child children

  birth="$(process_birth "$pid")"
  [ -n "$birth" ] || return 0
  printf '%s|%s\n' "$pid" "$birth"
  children="$(pgrep -P "$pid" 2>/dev/null || true)"
  for child in $children; do
    collect_process_tree "$child"
  done
}

signal_process_records() {
  local records="$1"
  local signal="$2"
  local pid birth

  while IFS='|' read -r pid birth; do
    [ -n "$pid" ] || continue
    if same_process "$pid" "$birth"; then
      kill "-$signal" "$pid" 2>/dev/null || true
    fi
  done <<EOF
$records
EOF
}

records_alive() {
  local records="$1"
  local pid birth

  while IFS='|' read -r pid birth; do
    [ -n "$pid" ] || continue
    if same_process "$pid" "$birth"; then
      return 0
    fi
  done <<EOF
$records
EOF
  return 1
}

stop_service() {
  local service="$1"
  local label pid pid_file records elapsed=0

  label="$(service_label "$service")"
  pid_file="$(service_pid_file "$service")"
  if ! pid="$(owned_pid "$service")"; then
    say "${label}未由一键脚本运行。"
    return 0
  fi

  process_matches_service "$service" "$pid" || {
    warn "${label} 所有权校验失败，未终止进程"
    return 1
  }
  records="$(collect_process_tree "$pid")"
  [ -n "$records" ] || {
    rm -f "$pid_file"
    say "${label}已停止。"
    return 0
  }

  say "正在停止${label}……"
  signal_process_records "$records" TERM
  while records_alive "$records" && [ "$elapsed" -lt "$STOP_TIMEOUT" ]; do
    sleep 1
    elapsed=$((elapsed + 1))
  done
  if records_alive "$records"; then
    warn "${label} 未在 ${STOP_TIMEOUT} 秒内退出，正在清理残留进程"
    signal_process_records "$records" KILL
    sleep 1
  fi

  rm -f "$pid_file"
  say "${label}已停止。"
}

rollback_started() {
  if [ "$STARTED_FRONTEND" -eq 1 ]; then
    stop_service frontend || true
    STARTED_FRONTEND=0
  fi
  if [ "$STARTED_BACKEND" -eq 1 ]; then
    stop_service backend || true
    STARTED_BACKEND=0
  fi
}

start_failed() {
  warn "$1"
  rollback_started
  fail "启动未完成。请查看 $RUNTIME_DIR 中的日志。"
}

start_service() {
  local service="$1"
  local label script log_file pid

  label="$(service_label "$service")"
  script="$(service_script "$service")"
  log_file="$(service_log_file "$service")"
  printf '\n[%s] 启动%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$label" >> "$log_file"
  say "正在启动${label}……"
  nohup "$script" >> "$log_file" 2>&1 </dev/null &
  pid=$!

  if ! write_pid_file "$service" "$pid"; then
    kill -TERM "$pid" 2>/dev/null || true
    return 1
  fi
  case "$service" in
    backend) STARTED_BACKEND=1 ;;
    frontend) STARTED_FRONTEND=1 ;;
  esac

  wait_for_health "$service" "$pid" || return 1
  say "${label}已就绪（PID ${pid}）。"
}

check_start_dependencies() {
  local command_name

  for command_name in curl lsof nohup pgrep ps; do
    require_command "$command_name"
  done
  validate_timeout
  [ -x "$BACKEND_SCRIPT" ] || fail "run_backend.sh 不存在或不可执行"
  [ -x "$FRONTEND_SCRIPT" ] || fail "run_frontend.sh 不存在或不可执行"
  [ -x "$BACKEND_DIR/.venv/bin/uvicorn" ] || fail "后端依赖未安装，请先完成 README 中的后端初始化"
  [ -d "$FRONTEND_DIR/node_modules" ] || fail "前端依赖未安装，请先在 frontend 目录运行 npm install"
  require_command npm
}

check_stop_dependencies() {
  local command_name

  for command_name in lsof pgrep ps; do
    require_command "$command_name"
  done
}

start_all() {
  local backend_pid=""
  local frontend_pid=""

  check_start_dependencies
  mkdir -p "$RUNTIME_DIR"

  backend_pid="$(owned_pid backend || true)"
  frontend_pid="$(owned_pid frontend || true)"

  if [ -z "$backend_pid" ] && port_in_use backend; then
    fail "端口 8000 已被其他进程占用；脚本不会接管或停止该进程"
  fi
  if [ -z "$frontend_pid" ] && port_in_use frontend; then
    fail "端口 5173 已被其他进程占用；脚本不会接管或停止该进程"
  fi

  if [ -n "$backend_pid" ]; then
    say "后端已经由一键脚本运行（PID ${backend_pid}），等待健康检查……"
    wait_for_health backend "$backend_pid" || fail "现有后端进程未通过健康检查，请先运行 stop_sandrone.command"
  else
    start_service backend || start_failed "后端未能在 ${START_TIMEOUT} 秒内就绪"
  fi

  if [ -n "$frontend_pid" ]; then
    say "前端已经由一键脚本运行（PID ${frontend_pid}），等待健康检查……"
    wait_for_health frontend "$frontend_pid" || start_failed "现有前端进程未通过健康检查"
  else
    start_service frontend || start_failed "前端未能在 ${START_TIMEOUT} 秒内就绪"
  fi

  STARTED_BACKEND=0
  STARTED_FRONTEND=0
  say ""
  say "Sandrone 本地服务已启动："
  say "  前端：http://127.0.0.1:5173"
  say "  后端：http://127.0.0.1:8000"
  say "  日志：$RUNTIME_DIR"
  say "双击 stop_sandrone.command 即可停止。"
}

stop_all() {
  check_stop_dependencies
  mkdir -p "$RUNTIME_DIR"
  stop_service frontend
  stop_service backend
  say "Sandrone 本地服务停止流程已完成。"
}

handle_interrupt() {
  warn "收到中断信号，正在回滚本次启动"
  rollback_started
  exit 130
}

case "${1:-}" in
  start)
    trap handle_interrupt INT TERM HUP
    start_all
    ;;
  stop)
    stop_all
    ;;
  *)
    fail "用法：$0 start|stop"
    ;;
esac
