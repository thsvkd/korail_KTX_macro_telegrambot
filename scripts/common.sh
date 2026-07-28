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

# has_venv - true when a pipenv virtualenv exists for this project
has_venv() {
    command -v pipenv >/dev/null 2>&1 && (cd "$ROOT_DIR" && pipenv --venv >/dev/null 2>&1)
}

# required_python - Python version requested by the Pipfile (e.g. "3.9")
required_python() {
    [[ -f "${ROOT_DIR}/Pipfile" ]] || return 0
    sed -n 's/^python_version *= *"\(.*\)"/\1/p' "${ROOT_DIR}/Pipfile" | head -n 1
}

# pipfile_index_url - package index declared in the Pipfile
pipfile_index_url() {
    local url=""
    [[ -f "${ROOT_DIR}/Pipfile" ]] && \
        url="$(sed -n 's/^url *= *"\(.*\)"/\1/p' "${ROOT_DIR}/Pipfile" | head -n 1)"
    echo "${url:-https://pypi.org/simple}"
}

# python_version_of <interpreter> - print its "major.minor"
python_version_of() {
    "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null
}

# find_python - absolute path to an interpreter this project can run on
#
# Prefers the version the Pipfile asks for. That version is often missing on
# developer machines (and 3.9 is end-of-life), so any 3.9+ interpreter is
# accepted rather than failing outright.
find_python() {
    local required candidate path
    local candidates=()

    required="$(required_python)"
    [[ -n "$required" ]] && candidates+=("python${required}")
    candidates+=(python3.13 python3.12 python3.11 python3.10 python3.9 python3)

    for candidate in "${candidates[@]}"; do
        path="$(command -v "$candidate" 2>/dev/null)" || continue
        if "$path" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            echo "$path"
            return 0
        fi
    done

    return 1
}

# python_runner - command prefix used to run project code
# Prefers the pipenv virtualenv, falls back to the system interpreter.
python_runner() {
    if has_venv; then
        echo "pipenv run python"
    else
        echo "python3"
    fi
}

# can_import <module> - true when the chosen runner can import the module
can_import() {
    # shellcheck disable=SC2046
    $(python_runner) -c "import $1" >/dev/null 2>&1
}
