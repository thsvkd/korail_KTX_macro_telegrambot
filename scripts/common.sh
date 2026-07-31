#!/usr/bin/env bash
# Shared helpers for the scripts in this directory.
# Sourced, not executed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Overridable so the scripts can be exercised against a scratch file.
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
ENV_EXAMPLE="${ROOT_DIR}/.env.example"

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_BLUE=$'\033[34m'
else
    C_RESET='' C_RED='' C_GREEN='' C_YELLOW='' C_BLUE=''
fi

info()  { printf '%s\n' "${C_BLUE}▶${C_RESET} $*"; }
ok()    { printf '%s\n' "${C_GREEN}✔${C_RESET} $*"; }
warn()  { printf '%s\n' "${C_YELLOW}⚠${C_RESET} $*" >&2; }
err()   { printf '%s\n' "${C_RED}✖${C_RESET} $*" >&2; }
die()   { err "$*"; exit 1; }

# require_cmd <command> [install hint]
require_cmd() {
    local cmd="$1"
    local hint="${2:-}"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        if [[ -n "$hint" ]]; then
            die "'$cmd' is required but not installed. $hint"
        fi
        die "'$cmd' is required but not installed."
    fi
}

# require_env_file - abort when .env is missing
require_env_file() {
    [[ -f "$ENV_FILE" ]] || die ".env not found. Run 'scripts/setup.sh' first."
}

# load_env - export every variable defined in .env
load_env() {
    require_env_file
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
}

# env_value <KEY> - print the value of KEY from .env (empty when unset)
env_value() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || return 0
    sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1
}

# set_env_key <KEY> <VALUE> - write KEY=VALUE into .env, replacing any existing
# line. Goes through python so values containing / & \ survive intact.
set_env_key() {
    local key="$1" value="$2"

    [[ -f "$ENV_FILE" ]] || : > "$ENV_FILE"

    KEY="$key" VALUE="$value" ENV_FILE="$ENV_FILE" python3 <<'PY'
import os

path = os.environ["ENV_FILE"]
key = os.environ["KEY"]
value = os.environ["VALUE"]

with open(path, encoding="utf-8") as handle:
    lines = handle.readlines()

replaced = False
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index] = f"{key}={value}\n"
        replaced = True
        break

if not replaced:
    if lines and not lines[-1].endswith("\n"):
        lines.append("\n")
    lines.append(f"{key}={value}\n")

with open(path, "w", encoding="utf-8") as handle:
    handle.writelines(lines)
PY

    chmod 600 "$ENV_FILE"
}

# gen_secret [bytes] - print a fresh URL-safe random secret
gen_secret() {
    local bytes="${1:-32}"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import secrets, sys; print(secrets.token_urlsafe(int(sys.argv[1])))" "$bytes"
    elif command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 "$bytes" | tr -d '\n/+='
    else
        head -c "$bytes" /dev/urandom | base64 | tr -d '\n/+='
    fi
}

# has_compose - true when either Compose CLI is available
has_compose() {
    docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1
}

# compose_version - print the Compose version string (empty when absent)
compose_version() {
    if docker compose version >/dev/null 2>&1; then
        docker compose version --short 2>/dev/null
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose version --short 2>/dev/null
    fi
}

# compose_supports_reset - true when the `!reset` YAML tag is understood.
# It landed in Compose v2.24; without it an override cannot drop a list
# inherited from the base file (sequences are appended, not replaced).
compose_supports_reset() {
    local version major minor
    version="$(compose_version)"
    version="${version#v}"
    [[ -n "$version" ]] || return 1

    major="${version%%.*}"
    minor="${version#*.}"
    minor="${minor%%.*}"

    [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || return 1
    (( major > 2 )) && return 0
    (( major == 2 && minor >= 24 ))
}

# compose - run docker compose with the repo's files, whichever CLI is present.
#
# Passing -f explicitly disables Compose's implicit loading of
# docker-compose.override.yml, so it has to be listed by hand.
compose() {
    local files=(-f "${ROOT_DIR}/docker-compose.yml")
    [[ -f "${ROOT_DIR}/docker-compose.override.yml" ]] && \
        files+=(-f "${ROOT_DIR}/docker-compose.override.yml")

    if docker compose version >/dev/null 2>&1; then
        docker compose "${files[@]}" "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose "${files[@]}" "$@"
    else
        # docker-compose-plugin lives in Docker's own APT repo, so it is not
        # installable on a system using the distro's docker.io package.
        # The plugin binary drops in per-user without root.
        err "Docker Compose is not installed."
        err "  Compose v2 (recommended, no root needed):"
        err "    mkdir -p ~/.docker/cli-plugins"
        err "    curl -SL \"https://github.com/docker/compose/releases/latest/download/docker-compose-linux-\$(uname -m)\" \\"
        err "      -o ~/.docker/cli-plugins/docker-compose"
        err "    chmod +x ~/.docker/cli-plugins/docker-compose"
        err "  Or the older v1 from the distro: sudo apt install docker-compose"
        die "Install one and run this again."
    fi
}

# require_uv - abort unless uv is installed
#
# uv owns the whole Python side now: it downloads the interpreter that
# pyproject.toml asks for, creates .venv, and installs from uv.lock. That
# replaces the interpreter search this file used to carry, which existed
# because pipenv gave up when the Pipfile's Python was not already present.
require_uv() {
    require_cmd uv \
        "Install it with 'curl -LsSf https://astral.sh/uv/install.sh | sh' or 'brew install uv'."
}

# has_venv - true when the project virtualenv exists
has_venv() {
    [[ -x "${ROOT_DIR}/.venv/bin/python" ]]
}

# python_runner - command prefix used to run project code
#
# 'uv run --frozen' brings .venv in line with uv.lock before running, without
# re-resolving, so there is no separate install step to forget. Unquoted at
# the call sites on purpose: this expands to several words.
python_runner() {
    require_uv
    echo "uv run --frozen --project ${ROOT_DIR} python"
}

# can_import <module> - true when the project environment can import the module
can_import() {
    # shellcheck disable=SC2046
    $(python_runner) -c "import $1" >/dev/null 2>&1
}

# ==================== The running bot ====================
#
# Two consumers of one bot token means Telegram hands each update to whichever
# asked first and answers the other with a 409, so a second copy of the bot is
# never something to start alongside the first. Everything below exists to
# find the one that is already running.

RUN_DIR="${ROOT_DIR}/.run"
PID_FILE="${RUN_DIR}/korail-bot.pid"
LOG_FILE="${RUN_DIR}/korail-bot.log"

# _pid_cmdline <pid> - the process's command line, spaces between arguments
_pid_cmdline() {
    local pid="$1"
    [[ -r "/proc/${pid}/cmdline" ]] || return 1
    tr '\0' ' ' < "/proc/${pid}/cmdline"
}

# _is_bot <pid> - true when the pid is really one of ours
#
# The number in the pidfile is only a claim. Pids get reused, so a stale file
# can name a process that has nothing to do with us, and signalling that would
# kill a stranger. The command line has to agree before we touch it.
_is_bot() {
    local pid="$1" cmd
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    cmd="$(_pid_cmdline "$pid")" || return 1
    [[ "$cmd" == *korail_bot.app* ]]
}

# bot_pids - every pid belonging to a running bot, one per line
#
# The pidfile is only the first place to look. The app can be started by hand
# and often is, and a copy started that way is exactly the duplicate this is
# meant to catch, so the process table is consulted too.
bot_pids_all() {
    local pid
    while read -r pid; do
        _is_bot "$pid" && echo "$pid"
    done < <({
        [[ -f "$PID_FILE" ]] && cat "$PID_FILE" 2>/dev/null
        # korail_bot[.]app: matches both `-m korail_bot.app` and the
        # `korail_bot.app:application` waitress serves, without the pattern
        # matching the pgrep that carries it.
        pgrep -f 'korail_bot[.]app' 2>/dev/null || true
    } | sort -u)
}

# bot_pids - one pid per running bot, the outermost process of each
#
# Started through 'uv run' the bot is a launcher holding an interpreter: two
# processes, one bot. Counting both would report a duplicate, and a duplicate
# is the thing worth shouting about, so anything whose parent is also on the
# list is dropped.
#
# For signalling use bot_pids_all instead. Stopping only the outermost would
# rely on the launcher passing the signal down, and a launcher that does not
# leaves the interpreter running - which is the duplicate this was supposed
# to prevent.
bot_pids() {
    local pid ppid found=()
    while read -r pid; do found+=("$pid"); done < <(bot_pids_all)

    for pid in "${found[@]:-}"; do
        [[ -n "$pid" ]] || continue
        ppid="$(awk '{print $4}' "/proc/${pid}/stat" 2>/dev/null || true)"
        if [[ -n "$ppid" ]] && printf '%s\n' "${found[@]}" | grep -qx "$ppid"; then
            continue
        fi
        echo "$pid"
    done
}

# bot_pid - the pid to report, empty when nothing is running
#
# Takes the first line with a parameter expansion rather than piping into
# head: under `set -o pipefail` head closes the pipe on the line it wanted and
# the producer dies of SIGPIPE, which then reads as the whole command failing.
bot_pid() {
    local pids
    pids="$(bot_pids)"
    [[ -n "$pids" ]] || return 0
    printf '%s\n' "${pids%%$'\n'*}"
}

# bot_uptime <pid> - how long the process has been up
bot_uptime() { ps -o etime= -p "$1" 2>/dev/null | tr -d ' '; }

# bot_stop [timeout] - stop the running bot and wait for it to be gone
#
# SIGTERM rather than SIGKILL, and then a wait: the app's handler takes its
# search processes down with it and deliberately leaves their records in
# Redis, which is what lets the next start pick the searches back up. Killed
# outright it would leave them orphaned, still logged in and still asking
# Korail for a train nobody is waiting for any more.
bot_stop() {
    local timeout="${1:-20}" pids waited=0
    pids="$(bot_pids_all | tr '\n' ' ')"
    [[ -n "${pids// /}" ]] || return 0

    info "Stopping the running bot (pid ${pids% })"
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null || true

    while (( waited < timeout )); do
        if [[ -z "$(bot_pids_all)" ]]; then
            rm -f "$PID_FILE"
            ok "Stopped"
            return 0
        fi
        sleep 1
        waited=$(( waited + 1 ))
    done

    warn "Still running after ${timeout}s - sending SIGKILL"
    # shellcheck disable=SC2046
    kill -KILL $(bot_pids_all | tr '\n' ' ') 2>/dev/null || true
    sleep 1
    rm -f "$PID_FILE"
    [[ -z "$(bot_pids_all)" ]] || die "Could not stop the bot"
    warn "Killed rather than asked. A search process may have outlived it;"
    warn "the next start reconciles whatever Redis still has a record of."
}

# ==================== Asking the user things ====================
#
# lower is used by ask_yn to accept Y as well as y.

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
#
# Prompts go to stderr so that the answer can be captured with $(...).
# Shared by setup.sh and onboarding.sh.

clean_default() {
    local value="$1"
    [[ "$value" =~ ^your_.*_here$ ]] && value=""
    printf '%s' "$value"
}

# ask <prompt> [default] - prompts on stderr so the answer can be captured
ask() {
    local prompt="$1" default="${2:-}" answer
    if [[ -n "$default" ]]; then
        printf '  %s [%s]: ' "$prompt" "$default" >&2
    else
        printf '  %s: ' "$prompt" >&2
    fi
    read -r answer
    printf '%s' "${answer:-$default}"
}

# ask_secret <prompt> - same, without echoing what is typed
ask_secret() {
    local prompt="$1" answer
    printf '  %s: ' "$prompt" >&2
    read -r -s answer
    printf '\n' >&2
    printf '%s' "$answer"
}

# ask_yn <prompt> [y|n] - returns 0 for yes
ask_yn() {
    local prompt="$1" default="${2:-y}" answer hint
    if [[ "$default" == "y" ]]; then hint="Y/n"; else hint="y/N"; fi
    while true; do
        printf '  %s [%s]: ' "$prompt" "$hint" >&2
        read -r answer
        answer="$(lower "${answer:-$default}")"
        case "$answer" in
            y|yes|예|ㅇ) return 0 ;;
            n|no|아니오|ㄴ) return 1 ;;
            *) printf '  %s\n' "y 또는 n으로 답해주세요." >&2 ;;
        esac
    done
}

