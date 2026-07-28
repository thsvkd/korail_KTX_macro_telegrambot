#!/usr/bin/env bash
#
# Prepare a development environment: dependencies, .env, secrets.
#
# Usage:
#   scripts/setup.sh            # install dev dependencies and create .env
#   scripts/setup.sh --no-deps  # only create .env and secrets

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

INSTALL_DEPS=1
for arg in "$@"; do
    case "$arg" in
        --no-deps) INSTALL_DEPS=0 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR"

# ---------------------------------------------------------------- .env file
if [[ -f "$ENV_FILE" ]]; then
    info ".env already exists - keeping it"
else
    [[ -f "$ENV_EXAMPLE" ]] || die ".env.example is missing"
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "Created .env from .env.example"
fi

# ------------------------------------------------------------------ secrets
info "Generating missing secrets"
"${SCRIPT_DIR}/gen-secrets.sh"

# ------------------------------------------------------------- dependencies
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
    require_cmd pipenv "Install it with 'pip install --user pipenv' or 'brew install pipenv'."

    if ! PYTHON_BIN="$(find_python)"; then
        err "No usable Python interpreter found (3.9 or newer is required)."
        err "Install one, then re-run this script. For example:"
        err "  apt install python3.11        # Debian/Ubuntu"
        err "  brew install python@3.11      # macOS"
        die "Or create the environment yourself: pipenv install --dev --python <path>"
    fi

    PYTHON_VERSION="$(python_version_of "$PYTHON_BIN")"
    REQUIRED_VERSION="$(required_python)"

    # pipenv otherwise looks for the Pipfile version and gives up when it is
    # missing, which is the common case now that 3.9 is end-of-life.
    if [[ -n "$REQUIRED_VERSION" && "$PYTHON_VERSION" != "$REQUIRED_VERSION" ]]; then
        warn "Pipfile targets Python ${REQUIRED_VERSION}, which is not installed."
        warn "Using ${PYTHON_BIN} (${PYTHON_VERSION}) instead."
        warn "Docker images still build on ${REQUIRED_VERSION}, so verify there before release."
    else
        info "Using ${PYTHON_BIN} (${PYTHON_VERSION})"
    fi

    # Pipfile.lock pins hashes from the index it was built against (PyPI).
    # Distro pip configs sometimes add another index - Raspberry Pi OS ships
    # /etc/pip.conf with piwheels - whose rebuilt wheels have different
    # hashes, so the install dies with "PACKAGES DO NOT MATCH THE HASHES".
    # Point the extra index back at the Pipfile's own source to neutralise it.
    # (An empty value does not work: pip then falls back to its config file.)
    if [[ -z "${PIP_EXTRA_INDEX_URL+x}" ]]; then
        export PIP_EXTRA_INDEX_URL="$(pipfile_index_url)"
    fi

    info "Installing Python dependencies (this can take a while)"
    if ! pipenv install --dev --python "$PYTHON_BIN"; then
        err "Dependency installation failed."
        err "If the lock file cannot be resolved on this Python version, try:"
        die "  pipenv install --dev --skip-lock --python ${PYTHON_BIN}"
    fi
    ok "Dependencies installed"
else
    info "Skipping dependency installation (--no-deps)"
fi

# ------------------------------------------------------------------ summary
echo
ok "Setup complete."
echo
echo "Still to do by hand:"
echo "  1. Put your bot token in .env         -> BOTTOKEN="
echo "  2. Register the webhook                -> scripts/set-webhook.sh https://your.domain/telebot"
echo "  3. Start the app                       -> scripts/run.sh"
echo
echo "Optional:"
echo "  ALLOW_LIST         restrict who may use the bot"
echo "  USERID / USERPW    Korail account for the magic-string shortcut"
echo "  ADMIN_MAGIC_STRING trigger for that shortcut (empty = disabled)"
