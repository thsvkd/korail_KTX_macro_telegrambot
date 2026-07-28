#!/usr/bin/env bash
# Shared helpers for the scripts in this directory.
# Sourced, not executed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
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

# compose - run docker compose with the repo's file, whichever CLI is present
compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose -f "${ROOT_DIR}/docker-compose.yml" "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose -f "${ROOT_DIR}/docker-compose.yml" "$@"
    else
        die "docker compose is not available. Install Docker Compose first."
    fi
}

# python_runner - echo the command prefix used to run project code
# Prefers the pipenv virtualenv, falls back to the system interpreter.
python_runner() {
    if command -v pipenv >/dev/null 2>&1 && pipenv --venv >/dev/null 2>&1; then
        echo "pipenv run python"
    else
        echo "python3"
    fi
}
