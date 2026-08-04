#!/usr/bin/env bash
# Private shared helpers for the scripts in this directory.
# Sourced, not executed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Overridable so the scripts can be exercised against a scratch file.
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
TEST_ENV_FILE="${TEST_ENV_FILE:-${ROOT_DIR}/.env.test}"
# Consumed by setup.sh after this file is sourced.
# shellcheck disable=SC2034
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

# require_env_file - abort when the selected configuration file is missing
require_env_file() {
    local label setup_command="scripts/setup.sh"
    label="${ENV_FILE#"$ROOT_DIR"/}"
    [[ "$ENV_FILE" == "$TEST_ENV_FILE" ]] && setup_command="scripts/setup.sh --test"
    [[ -f "$ENV_FILE" ]] || die "${label} not found. Run '${setup_command}' first."
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
# docker-compose.override.yml, so it has to be listed by hand for production.
# The onboarding override is deliberately production-only: it pins the local
# production image, which would otherwise replace IMAGE_NAME from .env.test
# and make a test deployment run the wrong build.
compose() {
    local files=(-f "${ROOT_DIR}/docker-compose.yml")
    [[ "$BOT_RUNTIME_PROFILE" != "test" && -f "${ROOT_DIR}/docker-compose.override.yml" ]] && \
        files+=(-f "${ROOT_DIR}/docker-compose.override.yml")
    # A caller-generated fragment, for settings that belong to one invocation
    # rather than to the checkout - `server.sh start --debug` raising the log
    # level without editing .env or leaving anything behind.
    [[ -n "${COMPOSE_EXTRA_FILE:-}" && -f "${COMPOSE_EXTRA_FILE}" ]] && \
        files+=(-f "$COMPOSE_EXTRA_FILE")

    # The tailscale sidecar sits behind a profile so that an install which
    # does not use the Mini App is unaffected by its existence. Selected here
    # rather than at each call site, so `up`, `ps`, `logs` and `down` all
    # agree about whether that container is part of this stack - a `down` that
    # forgot the profile would leave it running and still proxying.
    if tailscale_enabled && [[ ",${COMPOSE_PROFILES:-}," != *,tailscale,* ]]; then
        export COMPOSE_PROFILES="${COMPOSE_PROFILES:+${COMPOSE_PROFILES},}tailscale"
    fi

    if docker compose version >/dev/null 2>&1; then
        docker compose --env-file "$ENV_FILE" "${files[@]}" "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose --env-file "$ENV_FILE" "${files[@]}" "$@"
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

# use_test_stack - point Compose helpers at the isolated test-bot stack.
#
# The project name separates the generated network and named Redis volume.
# Container names and the published HTTP port live in .env.test itself so a
# plain `docker compose config` also describes the same isolated stack.
use_test_stack() {
    ENV_FILE="$TEST_ENV_FILE"
    export ENV_FILE
    if [[ -f "$ENV_FILE" ]]; then
        COMPOSE_PROJECT_NAME="$(env_value COMPOSE_PROJECT_NAME)"
    fi
    COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-korail-bot-test}"
    BOT_RUNTIME_PROFILE="test"
    export COMPOSE_PROJECT_NAME
    export BOT_RUNTIME_PROFILE
}

# redis_data_dir - absolute host path holding the selected stack's Redis state
#
# docker-compose.yml bind-mounts this instead of using a named volume, so the
# data outlives every container and every volume command. Relative values are
# read the way compose reads them: against the repository root.
redis_data_dir() {
    local dir
    dir="$(env_value REDIS_DATA_DIR)"
    dir="${dir:-./.data/redis}"
    if [[ "$dir" != /* ]]; then
        dir="${ROOT_DIR}/${dir#./}"
    fi
    printf '%s' "$dir"
}

# ensure_redis_data_dir - create the bind-mount source before compose does
#
# Compose creates a missing bind source itself, owned by root. Creating it here
# first leaves the directory owned by whoever runs these scripts, so backing it
# up does not need sudo. Redis chowns the files it writes inside either way.
ensure_redis_data_dir() {
    mkdir -p "$(redis_data_dir)"
}

# ---------------------------------------------------------------- tailscale
#
# The Mini App needs the bot to be reachable from a phone, and the sidecar in
# docker-compose.yml is how. It joins the tailnet as a node of its own - its
# own name, its own address - rather than borrowing the host's, so the bot
# gets a URL that is not shared with whatever else this machine serves.
#
# Two things it is deliberately not: it is not on the host's network, and it
# is not given the host's tailscale state. A container that could rewrite the
# host's tailnet identity would be a much larger thing to trust than a proxy.

# tailscale_enabled - true when this stack has a sidecar to start
#
# Keyed on the node name rather than on the auth key. Naming the node is the
# decision - it is what the URL is made of - and the key is only one of two
# ways to authorise it. The other is clicking a link, which is how a node with
# no key joins, so keying on the key would have meant the interactive path
# could never start the container that prints the link.
tailscale_enabled() {
    [[ -n "$(clean_default "$(env_value TS_HOSTNAME)")" ]]
}

# tailscale_hostname - the node name, which decides the public URL
tailscale_hostname() {
    local name
    name="$(env_value TS_HOSTNAME)"
    printf '%s' "${name:-korail-bot}"
}

# tailscale_authenticated - true once the node has joined a tailnet
tailscale_authenticated() {
    local container
    container="$(compose_container tailscale)"
    container_running "$container" || return 1

    [[ "$(docker exec "$container" tailscale status --json 2>/dev/null \
        | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("BackendState", ""))
except Exception:
    print("")' 2>/dev/null)" == "Running" ]]
}

# tailscale_login_url - the link that authorises a node started without a key
#
# Read out of the container's log rather than asked of the CLI: tailscaled
# prints it once while it waits, and at that point `tailscale status` has no
# tailnet to answer about.
tailscale_login_url() {
    local container
    container="$(compose_container tailscale)"
    container_exists "$container" || return 1

    docker logs "$container" 2>&1 \
        | grep -oE 'https://login\.tailscale\.com/a/[0-9a-f]+' \
        | tail -n 1
}

# tailscale_login_begin - put a link on screen and leave something waiting
#
# Two problems shaped this, and both were found the hard way.
#
# The sidecar image cannot do the login itself. Its entrypoint gives
# `tailscale up` sixty seconds, kills it, and exits; Docker restarts the
# container, which registers a *new* node key and so invalidates the link it
# printed a minute earlier. Anyone who was not already looking at the terminal
# is chasing a link that died before they read it.
#
# The obvious fix - run the login by hand and wait for the click - only moves
# the deadline. It puts a person inside the runtime of a command, so walking
# away for ten minutes ends the attempt, and the next attempt prints a
# different link. That happened twice here before this shape existed.
#
# So nothing waits on the person. The helper container waits, indefinitely,
# in the background; this function starts it if it is not already running and
# prints its link. Whenever the click lands, the state directory becomes
# authorised, and the next `deploy.sh up` picks it up and carries on. Running
# this repeatedly is safe and keeps showing the same link.
tailscale_login_begin() {
    local state name helper url deadline
    state="$(tailscale_state_dir)"
    name="$(tailscale_hostname)"
    helper="$(compose_container tailscale)_login"

    mkdir -p "$state"

    if ! container_running "$helper"; then
        # A stopped one has a dead link in it and holds the name.
        docker rm -f "$helper" >/dev/null 2>&1 || true

        # No --timeout: this is meant to outlast the person's coffee break.
        # tailscaled and the login run in the same container so they share the
        # socket, and the state directory is the one the sidecar will use.
        docker run -d --name "$helper" \
            --restart unless-stopped \
            -v "${state}:/var/lib/tailscale" \
            --entrypoint /bin/sh \
            "tailscale/tailscale:${TS_IMAGE_TAG:-stable}" \
            -c "tailscaled --tun=userspace-networking --statedir=/var/lib/tailscale \
                    --socket=/tmp/tailscaled.sock &
                sleep 3
                exec tailscale --socket=/tmp/tailscaled.sock up --hostname='${name}'" \
            >/dev/null || {
            err "Could not start the login helper."
            return 1
        }
    fi

    deadline=$(( SECONDS + 60 ))
    until url="$(docker logs "$helper" 2>&1 \
        | grep -oE 'https://login\.tailscale\.com/a/[0-9a-f]+' | tail -n 1)" \
        && [[ -n "$url" ]]; do
        if (( SECONDS >= deadline )); then
            err "The login helper printed no link. Its log:"
            docker logs "$helper" 2>&1 | tail -20 >&2
            return 1
        fi
        sleep 2
    done

    printf '%s' "$url"
}

# tailscale_login_finish - clear away the helper once the node has joined
tailscale_login_finish() {
    docker rm -f "$(compose_container tailscale)_login" >/dev/null 2>&1 || true
}

# tailscale_state_authorised - whether the stored state has joined a tailnet
#
# A heuristic, and deliberately one: the authoritative answer needs a running
# tailscaled, and the point of asking is to decide whether to start one. An
# unauthorised state directory holds only tailscaled.state and its logs; the
# profile appears when a node is accepted. Being wrong here costs a prompt,
# not correctness - the sidecar itself is what actually authenticates.
tailscale_state_authorised() {
    [[ -d "$(tailscale_state_dir)/profile-data" ]]
}

# tailscale_serve_mode - `serve` (tailnet only) or `funnel` (public)
tailscale_serve_mode() {
    local mode
    mode="$(env_value TS_SERVE_MODE)"
    [[ "$mode" == "funnel" ]] && { printf 'funnel'; return; }
    printf 'serve'
}

# tailscale_config_dir - host directory holding the generated serve config
#
# Generated rather than committed, and a directory rather than a file for two
# separate reasons. The port it proxies to comes from MINI_APP_API_PORT, so a
# checked-in file would go stale the moment someone changed that. And
# tailscaled only notices later edits when the mount is a directory.
tailscale_config_dir() {
    local dir
    dir="$(env_value TS_CONFIG_DIR)"
    dir="${dir:-./.data/tailscale/config}"
    [[ "$dir" != /* ]] && dir="${ROOT_DIR}/${dir#./}"
    printf '%s' "$dir"
}

# tailscale_state_dir - host directory holding the node's identity
tailscale_state_dir() {
    local dir
    dir="$(env_value TS_STATE_DIR)"
    dir="${dir:-./.data/tailscale/state}"
    [[ "$dir" != /* ]] && dir="${ROOT_DIR}/${dir#./}"
    printf '%s' "$dir"
}

# write_tailscale_serve_config <serve|funnel> - generate the sidecar's config
#
# AllowFunnel is the whole difference between the two modes: false keeps the
# address inside the tailnet, true puts it on the internet. Generating both
# from one place means the two cannot drift apart in what they proxy to.
#
# ${TS_CERT_DOMAIN} is left for the container to substitute - it is the node's
# own name, which is not known here and does not need to be.
write_tailscale_serve_config() {
    local mode="${1:-serve}" allow="false" port dir
    [[ "$mode" == "funnel" ]] && allow="true"

    port="$(env_value MINI_APP_API_PORT)"
    port="${port:-8081}"
    dir="$(tailscale_config_dir)"
    mkdir -p "$dir"

    cat > "${dir}/serve.json" <<EOF
{
  "TCP": { "443": { "HTTPS": true } },
  "Web": {
    "\${TS_CERT_DOMAIN}:443": {
      "Handlers": { "/": { "Proxy": "http://app:${port}" } }
    }
  },
  "AllowFunnel": { "\${TS_CERT_DOMAIN}:443": ${allow} }
}
EOF
}

# tailscale_url - the address the running sidecar actually answers on
#
# Asked of the container rather than assembled from the hostname, because the
# tailnet's domain is not something this checkout knows and a guessed URL that
# almost works is worse than none.
tailscale_url() {
    local container
    container="$(compose_container tailscale)"
    container_running "$container" || return 1

    docker exec "$container" tailscale status --json 2>/dev/null \
        | python3 -c 'import json,sys
try:
    name = json.load(sys.stdin)["Self"]["DNSName"].rstrip(".")
except Exception:
    raise SystemExit(1)
print(f"https://{name}/" if name else "", end="")' 2>/dev/null
}

# compose_container <app|redis|tailscale> - the container name the stack uses
compose_container() {
    local name
    case "$1" in
        app)   name="$(env_value APP_CONTAINER_NAME)";   name="${name:-korail_bot}" ;;
        redis) name="$(env_value REDIS_CONTAINER_NAME)"; name="${name:-korail_redis}" ;;
        tailscale)
            name="$(env_value TS_CONTAINER_NAME)"; name="${name:-korail_tailscale}" ;;
        *)     return 1 ;;
    esac
    printf '%s' "$name"
}

# container_running <name> - true when a container by that name is up
container_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$1"
}

# container_exists <name> - true when it is up, stopped or merely created
container_exists() {
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$1"
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
# asked first and answers the other with a 409. Production and test may run
# together only because they have different tokens and runtime profiles;
# everything below finds processes belonging to the selected profile alone.
#
# Discovery is by command line and runtime profile, never by which script did
# the starting, so a bot started by an older revision of these scripts is still
# found and still stops cleanly.

RUN_DIR="${ROOT_DIR}/.run"
PID_FILE="${RUN_DIR}/korail-bot.pid"
# Consumed by server.sh after this file is sourced.
# shellcheck disable=SC2034
LOG_FILE="${RUN_DIR}/korail-bot.log"
BOT_RUNTIME_PROFILE="${BOT_RUNTIME_PROFILE:-production}"

# use_test_runtime - select the host-side test bot managed by server.sh.
#
# Compose only needs use_test_stack. A host process additionally needs its own
# pidfile and log so starting or stopping it cannot touch the production bot.
use_test_runtime() {
    use_test_stack
    BOT_RUNTIME_PROFILE="test"
    PID_FILE="${RUN_DIR}/korail-bot-test.pid"
    # Read by server.sh, which sources this file.
    # shellcheck disable=SC2034
    LOG_FILE="${RUN_DIR}/korail-bot-test.log"
    export BOT_RUNTIME_PROFILE
}

# _pid_cmdline <pid> - the process's command line, spaces between arguments
_pid_cmdline() {
    local pid="$1"
    [[ -r "/proc/${pid}/cmdline" ]] || return 1
    tr '\0' ' ' < "/proc/${pid}/cmdline"
}

# _pid_runtime_profile <pid> - the run.sh profile inherited by one process.
#
# Processes started before profiles existed have no marker and are production
# by definition. Only this one non-secret value is extracted from /proc; the
# rest of the environment includes credentials and must never be printed.
_pid_runtime_profile() {
    local pid="$1" environ profile
    [[ -r "/proc/${pid}/environ" ]] || return 1
    environ="$(tr '\0' '\n' < "/proc/${pid}/environ")" || return 1
    # A readable file that reads empty is not a process without a marker. It
    # is a process whose memory image is not there to be read, which is what
    # /proc shows for the moment execve takes to swap one program for another
    # - measurably, on a loaded machine, a couple of percent of the time a
    # process is looked at right after it is started.
    #
    # Falling through to the default below would call that process production,
    # and the one time it matters is exactly when it happens: a test bot in
    # the middle of starting, seen by whoever is stopping the production one.
    # Unknown is the honest answer, and it keeps _is_bot from matching either
    # profile rather than guessing which.
    [[ -n "$environ" ]] || return 1
    profile="$(printf '%s\n' "$environ" | sed -n 's/^BOT_RUNTIME_PROFILE=//p' | tail -n 1)"
    printf '%s' "${profile:-production}"
}

# load_bot_runtime_env <pid> - import only status-relevant configuration
#
# A status command may be run from another git worktree than the process it
# found. Its local .env then describes a different Redis and cannot be used to
# inspect the running bot. Reading a fixed allow-list from /proc avoids both
# sourcing another checkout's arbitrary file and importing unrelated secrets
# such as Telegram or railway credentials.
load_bot_runtime_env() {
    local pid="$1" entry key value
    [[ -r "/proc/${pid}/environ" ]] || return 1

    while IFS= read -r -d '' entry; do
        [[ "$entry" == *=* ]] || continue
        key="${entry%%=*}"
        value="${entry#*=}"
        case "$key" in
            FLASK_HOST|FLASK_PORT|LOG_LEVEL|SEARCH_INTERVAL|SEARCH_INTERVAL_JITTER|\
            SEARCH_FAILURE_ALERT_THRESHOLD|REDIS_HOST|REDIS_PORT|REDIS_DB|\
            REDIS_PASSWORD|REDIS_DECODE_RESPONSES|REDIS_SOCKET_TIMEOUT|\
            REDIS_SOCKET_CONNECT_TIMEOUT|REDIS_MAX_CONNECTIONS|REDIS_CONTAINER_NAME|\
            DEV_REDIS_CONTAINER_NAME|DEV_REDIS_PORT|SESSION_SECRET)
                printf -v "$key" '%s' "$value"
                # The name is in the variable on purpose - the allow-list above
                # is the point, and each match exports the variable it named.
                # shellcheck disable=SC2163
                export "$key"
                ;;
        esac
    done < "/proc/${pid}/environ"
    return 0
}

# _in_container <pid> - true when the pid belongs to a container, not this host
#
# A container's processes are in the host process table too, so pgrep finds the
# bot inside the compose stack and it carries the same command line and the same
# BOT_RUNTIME_PROFILE. Nothing below could tell it apart from a host process,
# and the consequence is not cosmetic: `stop` would signal into the container,
# which compose then restarts, and `status` would report the containerised bot
# as a second copy fighting for the token.
#
# The mount namespace is the discriminator. Every container has its own; a host
# process shares this script's.
_in_container() {
    local pid="$1" theirs mine
    theirs="$(readlink "/proc/${pid}/ns/mnt" 2>/dev/null)" || return 1
    mine="$(readlink /proc/self/ns/mnt 2>/dev/null)" || return 1
    [[ -n "$theirs" && "$theirs" != "$mine" ]]
}

# _is_bot <pid> - true when the pid is really one of ours, running on this host
#
# The number in the pidfile is only a claim. Pids get reused, so a stale file
# can name a process that has nothing to do with us, and signalling that would
# kill a stranger. The command line has to agree before we touch it.
_is_bot() {
    local pid="$1" cmd profile
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    _in_container "$pid" && return 1
    cmd="$(_pid_cmdline "$pid")" || return 1
    [[ "$cmd" == *korail_bot.app* ]] || return 1
    profile="$(_pid_runtime_profile "$pid")" || return 1
    [[ "$profile" == "$BOT_RUNTIME_PROFILE" ]]
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
    # Seeing another runtime profile is an expected non-match, not an error.
    # Without an explicit success status, the final `_is_bot` result leaks out
    # of the while loop and `set -e -o pipefail` can abort a test-bot start
    # merely because the production bot is already running (or vice versa).
    return 0
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
# Shared by setup.sh's standard and onboarding flows.

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
