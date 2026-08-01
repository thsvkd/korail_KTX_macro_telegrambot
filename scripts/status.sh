#!/usr/bin/env bash
#
# Report on the bot: whether it is running, and what it is doing.
#
# Reads only. Safe to run against a live bot, and meant to be - the searches
# it lists are the ones happening right now.
#
# Usage:
#   scripts/status.sh                # the report
#   scripts/status.sh --log [N]      # the report, plus the last N log lines
#
#   scripts/status.sh logs           # just the log, last 20 lines
#   scripts/status.sh logs 100       # just the log, last 100 lines
#   scripts/status.sh logs -f        # follow it (Ctrl-C to stop)
#   scripts/status.sh redis [--keys|COMMAND ...]
#   scripts/status.sh --test          # report the run.sh --test instance
#   scripts/status.sh --test redis    # inspect the isolated test Redis
#
# Exit status is 0 when the bot is running, 1 when it is not, so it can gate
# something else: scripts/status.sh >/dev/null || scripts/run.sh --daemon

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

TEST_RUNTIME=0
FILTERED_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--test" ]]; then
        TEST_RUNTIME=1
    else
        FILTERED_ARGS+=("$arg")
    fi
done
set -- "${FILTERED_ARGS[@]}"

if (( TEST_RUNTIME )); then
    use_test_runtime
else
    BOT_RUNTIME_PROFILE="production"
    export BOT_RUNTIME_PROFILE
fi

status_redis() {

case "${1:-}" in
    -h|--help) printf '%s\n' 'Usage: scripts/status.sh [--test] redis [--keys|COMMAND ...]'; exit 0 ;;
esac

require_cmd docker

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
load_env

runtime_pid="$(bot_pid)"
if [[ -n "$runtime_pid" ]]; then
    load_bot_runtime_env "$runtime_pid" || true
fi

PASSWORD="${REDIS_PASSWORD:-}"
[[ -n "$PASSWORD" ]] || die "REDIS_PASSWORD is not set in ${ENV_FILE#"$ROOT_DIR"/}"

# Either the compose stack or the standalone instance scripts/run.sh redis
# starts for host-side runs; whichever is up is the one to talk to.
CONTAINER=""
if (( TEST_RUNTIME )); then
    CANDIDATES=(
        "${REDIS_CONTAINER_NAME:-korail_redis_test}"
        "${DEV_REDIS_CONTAINER_NAME:-korail_test_dev_redis}"
    )
else
    CANDIDATES=(korail_redis korail_dev_redis)
fi
for candidate in "${CANDIDATES[@]}"; do
    if docker ps --format '{{.Names}}' | grep -qx "$candidate"; then
        CONTAINER="$candidate"
        break
    fi
done

if [[ -z "$CONTAINER" ]]; then
    err "No Redis container is running."
    if (( TEST_RUNTIME )); then
        err "  compose stack:     scripts/deploy.sh --test up"
        die "  local development: scripts/run.sh --test redis"
    fi
    err "  compose stack:     scripts/deploy.sh up"
    die "  local development: scripts/run.sh redis"
fi

if [[ "${1:-}" == "--keys" ]]; then
    info "Key space summary"
    for prefix in user_session running_reservation payment_status \
                  multi_reservation_status partial_reservations \
                  admin_authenticated admin_auth_failures subscribers; do
        count="$(docker exec "$CONTAINER" redis-cli -a "$PASSWORD" --no-auth-warning \
            --scan --pattern "${prefix}*" 2>/dev/null | wc -l | tr -d ' ')"
        printf '  %-28s %s\n' "$prefix" "$count"
    done
    exit 0
fi

# -t only when there is a terminal to allocate: with piped stdin, docker
# refuses outright with "the input device is not a TTY", which makes this
# script unusable from a script or a pipeline.
DOCKER_FLAGS=(-i)
[[ -t 0 ]] && DOCKER_FLAGS+=(-t)

exec docker exec "${DOCKER_FLAGS[@]}" "$CONTAINER" \
    redis-cli -a "$PASSWORD" --no-auth-warning "$@"

}

if [[ "${1:-}" == "redis" ]]; then
    shift
    status_redis "$@"
    exit
fi


SHOW_LOG=0
LOG_LINES=20
LOGS_ONLY=0
FOLLOW=0

# 'logs' means "skip the report, I want the log". It remains a subcommand
# because following a log is a different thing to do, not a report detail.
if [[ "${1:-}" == "logs" ]]; then
    LOGS_ONLY=1
    SHOW_LOG=1
    shift
fi

while (( $# )); do
    case "$1" in
        --log) SHOW_LOG=1; [[ "${2:-}" =~ ^[0-9]+$ ]] && { LOG_LINES="$2"; shift; } ;;
        -f|--follow) FOLLOW=1 ;;
        logs) LOGS_ONLY=1; SHOW_LOG=1 ;;
        [0-9]*) LOG_LINES="$1" ;;
        -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
    shift
done

(( FOLLOW )) && (( ! LOGS_ONLY )) && die "--follow only makes sense with 'logs' (try: scripts/status.sh logs -f)"

# ==================== Just the log ====================

if (( LOGS_ONLY )); then
    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    if [[ ! -f "$LOG_FILE" ]]; then
        # A foreground run logs to its terminal, so there is nothing to read.
        err "${LOG_FILE#"$ROOT_DIR"/} 이 없습니다."
        err "포그라운드로 실행 중이면 그 터미널에 출력되고, 아직 한 번도"
        if (( TEST_RUNTIME )); then
            die "데몬으로 띄운 적이 없다면 scripts/run.sh --test --daemon 으로 시작하세요."
        fi
        die "데몬으로 띄운 적이 없다면 scripts/run.sh --daemon 으로 시작하세요."
    fi

    if (( FOLLOW )); then
        # -F, not -f: keeps following if the file is ever replaced.
        exec tail -n "$LOG_LINES" -F "$LOG_FILE"
    fi
    exec tail -n "$LOG_LINES" "$LOG_FILE"
fi

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

heading() { printf '\n%s\n' "${C_BLUE}── $* ${C_RESET}"; }
field()   { printf '  %-22s %s\n' "$1" "$2"; }

# ==================== The process ====================

heading "봇 프로세스"

PID="$(bot_pid)"
if [[ -z "$PID" ]]; then
    err "실행 중이 아님"
    [[ -f "$PID_FILE" ]] && field "남은 pidfile" "${PID_FILE#"$ROOT_DIR"/} (죽은 프로세스를 가리킴)"
    if (( TEST_RUNTIME )); then
        field "기동" "scripts/run.sh --test --daemon"
    else
        field "기동" "scripts/run.sh --daemon"
    fi
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
CONFIG_SOURCE="${ENV_FILE#"$ROOT_DIR"/}"
if (( TEST_RUNTIME )); then
    export BOT_RUNTIME_PROFILE="test"
    export FLASK_PORT="${FLASK_PORT:-8081}"
    export REDIS_HOST="127.0.0.1"
    export REDIS_PORT="${DEV_REDIS_PORT:-6380}"
else
    export BOT_RUNTIME_PROFILE="production"
fi
if (( RUNNING )) && load_bot_runtime_env "$PID"; then
    CONFIG_SOURCE="실행 중인 pid ${PID}의 환경"
fi
[[ "${REDIS_HOST:-}" == "redis" ]] && export REDIS_HOST=localhost

heading "설정"
field "런타임" "$BOT_RUNTIME_PROFILE"
field "설정 원본" "$CONFIG_SOURCE"
field "Telegram updates" "long polling"
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
    if (( TEST_RUNTIME )); then
        err "Redis ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379} 무응답 - 'scripts/run.sh --test redis' 로 기동"
    else
        err "Redis ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379} 무응답 - 'scripts/run.sh redis' 로 기동"
    fi
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
        print(
            f"  {mark}  chat_id={r.chat_id}  "
            f"{p.rail_operator.display_name}={mask_phone(r.korail_id)}"
        )
        print(f"      {p.src_locate} → {p.dst_locate}  {p.dep_date}  "
              f"{p.dep_time[:4]}~{p.max_dep_time}  {p.train_type_display}  "
              f"{p.passenger_count}명  {p.seat_strategy}")
        print(f"      pid={r.process_id}  run_id={r.run_id or '(없음)'}")
        if not running:
            print("      ⚠ 기록만 남고 검색은 죽었습니다. 봇을 재시작하면 정리/재개합니다.")

scheduled = storage.get_all_scheduled_searches()
if scheduled:
    print()
    for s in sorted(scheduled, key=lambda s: s.start_at):
        p = s.search_params
        print(
            f"  ⏰ 예약 대기  chat_id={s.chat_id}  "
            f"{p.rail_operator.display_name}={mask_phone(s.korail_id)}"
        )
        print(f"      시작 {s.start_at:%m/%d %H:%M}  ({s.seconds_until_due() / 60:.0f}분 뒤)")
        print(f"      {p.src_locate} → {p.dst_locate}  {p.dep_date}  "
              f"{p.dep_time[:4]}~{p.max_dep_time}  {p.passenger_count}명")

payments = storage.get_all_payment_statuses()
pending = [s for s in payments if not s.completed]
if pending:
    print()
    for s in pending:
        state = "알림 동작 중" if s.reminder_active else "알림 꺼짐"
        since = s.created_at.strftime("%H:%M:%S") if s.created_at else "?"
        print(f"  💳 결제 대기  chat_id={s.chat_id}  {state}  (시작 {since})")

if not reservations and not scheduled and not pending:
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
