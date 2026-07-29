#!/usr/bin/env bash
#
# Report on the bot: whether it is running, and what it is doing.
#
# Reads only. Safe to run against a live bot, and meant to be - the searches
# it lists are the ones happening right now.
#
# Usage:
#   scripts/status.sh              # the report
#   scripts/status.sh --log [N]    # also print the last N log lines (default 20)
#
# Exit status is 0 when the bot is running, 1 when it is not, so it can gate
# something else: scripts/status.sh >/dev/null || scripts/run.sh --daemon

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

SHOW_LOG=0
LOG_LINES=20

while (( $# )); do
    case "$1" in
        --log) SHOW_LOG=1; [[ "${2:-}" =~ ^[0-9]+$ ]] && { LOG_LINES="$2"; shift; } ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
    shift
done

cd "$ROOT_DIR"

heading() { printf '\n%s\n' "${C_BLUE}── $* ${C_RESET}"; }
field()   { printf '  %-22s %s\n' "$1" "$2"; }

# ==================== The process ====================

heading "봇 프로세스"

PID="$(bot_pid)"
if [[ -z "$PID" ]]; then
    err "실행 중이 아님"
    [[ -f "$PID_FILE" ]] && field "남은 pidfile" "${PID_FILE#"$ROOT_DIR"/} (죽은 프로세스를 가리킴)"
    field "기동" "scripts/run.sh --daemon"
    RUNNING=0
else
    RUNNING=1
    ok "실행 중"
    field "pid" "$PID"
    field "가동 시간" "$(bot_uptime "$PID")"
    rss="$(awk '/VmRSS/{print $2" "$3}' "/proc/${PID}/status" 2>/dev/null || true)"
    field "메모리(RSS)" "${rss:-?}"

    # Foreground and daemon runs are told apart by who the parent is: a
    # daemon was orphaned to init when the script that started it exited.
    ppid="$(awk '{print $4}' "/proc/${PID}/stat" 2>/dev/null || echo 0)"
    if [[ "$ppid" == "1" ]]; then
        field "기동 방식" "데몬 (--daemon)"
    else
        field "기동 방식" "포그라운드 (부모 pid ${ppid})"
    fi

    # || true on every one of these: grep exits 1 when it matches nothing,
    # and under `set -e` with pipefail that ends the report rather than
    # leaving a field blank.
    cmdline="$(_pid_cmdline "$PID" 2>/dev/null || true)"
    listen="$(printf '%s' "$cmdline" | grep -o -- '--listen=[^ ]*' || true)"
    [[ -n "$listen" ]] && field "listen" "${listen#--listen=}"

    extra="$(bot_pids | { grep -vx "$PID" || true; } | tr '\n' ' ')"
    if [[ -n "${extra// /}" ]]; then
        warn "봇 프로세스가 여러 개입니다: ${extra% } - 텔레그램 409 의 원인이 됩니다"
    fi
fi

if [[ -f "$LOG_FILE" ]]; then
    field "로그" "${LOG_FILE#"$ROOT_DIR"/} ($(du -h "$LOG_FILE" 2>/dev/null | cut -f1))"
fi

# ==================== Configuration and reachability ====================

load_env
[[ "${REDIS_HOST:-}" == "redis" ]] && export REDIS_HOST=localhost

heading "설정"
field "RECEIVE_MODE" "${RECEIVE_MODE:-polling}"
field "LOG_LEVEL" "${LOG_LEVEL:-INFO}"
field "검색 간격" "${SEARCH_INTERVAL:-1}초 (지터 ${SEARCH_INTERVAL_JITTER:-0.4})"
field "장애 알림" "연속 ${SEARCH_FAILURE_ALERT_THRESHOLD:-10}회 실패 시"

heading "연결"

listen_port="${FLASK_PORT:-8080}"
if PORT="$listen_port" python3 -c '
import os, socket, sys
try:
    with socket.create_connection(("127.0.0.1", int(os.environ["PORT"])), timeout=2):
        pass
except OSError:
    sys.exit(1)
' 2>/dev/null; then
    ok "HTTP 127.0.0.1:${listen_port} 응답"
else
    if (( RUNNING )); then
        err "HTTP 127.0.0.1:${listen_port} 무응답 - 봇은 살아 있는데 포트가 안 열렸습니다"
    else
        field "HTTP ${listen_port}" "닫힘 (봇이 꺼져 있으니 정상)"
    fi
fi

redis_up=0
if REDIS_HOST="${REDIS_HOST:-localhost}" REDIS_PORT="${REDIS_PORT:-6379}" python3 -c '
import os, socket, sys
try:
    with socket.create_connection((os.environ["REDIS_HOST"], int(os.environ["REDIS_PORT"])), timeout=2):
        pass
except OSError:
    sys.exit(1)
' 2>/dev/null; then
    ok "Redis ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379} 응답"
    redis_up=1
else
    err "Redis ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379} 무응답 - 'scripts/dev-redis.sh' 로 기동"
fi

# ==================== What it is working on ====================

if (( redis_up )) && has_venv; then
    heading "진행 중인 작업"
    # Through the project's own storage layer rather than redis-cli, so the
    # records are read with the same deserialisation the bot uses and a
    # format change cannot make this quietly wrong.
    # LOG_LEVEL=CRITICAL: opening the storage logs that it connected, which
    # belongs in the bot's log and not in the middle of this report.
    # shellcheck disable=SC2046
    LOG_LEVEL=CRITICAL $(python_runner) - <<'PY' || warn "Redis 상태를 읽지 못했습니다"
import os
import sys

from korail_bot.storage.redis import RedisStorage
from korail_bot.utils.privacy import mask_phone


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


storage = RedisStorage()

reservations = storage.get_all_running_reservations()
if not reservations:
    print("  검색 중인 예약 없음")
else:
    for r in reservations:
        p = r.search_params
        running = alive(r.process_id)
        mark = "\033[32m●\033[0m 검색 중" if running else "\033[31m●\033[0m 프로세스 없음"
        print(f"  {mark}  chat_id={r.chat_id}  코레일={mask_phone(r.korail_id)}")
        print(f"      {p.src_locate} → {p.dst_locate}  {p.dep_date}  "
              f"{p.dep_time[:4]}~{p.max_dep_time}  {p.train_type_display}  "
              f"{p.passenger_count}명  {p.seat_strategy}")
        print(f"      pid={r.process_id}  run_id={r.run_id or '(없음)'}")
        if not running:
            print("      ⚠ 기록만 남고 검색은 죽었습니다. 봇을 재시작하면 정리/재개합니다.")

payments = storage.get_all_payment_statuses()
pending = [s for s in payments if not s.completed]
if pending:
    print()
    for s in pending:
        state = "알림 동작 중" if s.reminder_active else "알림 꺼짐"
        since = s.created_at.strftime("%H:%M:%S") if s.created_at else "?"
        print(f"  💳 결제 대기  chat_id={s.chat_id}  {state}  (시작 {since})")

if not reservations and not pending:
    sys.exit(0)
PY
fi

# ==================== Log ====================

if (( SHOW_LOG )); then
    heading "로그 마지막 ${LOG_LINES}줄"
    if [[ -f "$LOG_FILE" ]]; then
        tail -n "$LOG_LINES" "$LOG_FILE" | sed 's/^/  /'
    else
        # A foreground run logs to its terminal, so there is nothing to tail.
        printf '  %s\n' "${LOG_FILE#"$ROOT_DIR"/} 이 없습니다 (포그라운드 실행은 터미널로 출력합니다)"
    fi
fi

echo
(( RUNNING )) || exit 1
