#!/usr/bin/env bash
#
# Prepare a development environment: dependencies, .env, secrets.
#
# Usage:
#   scripts/setup.sh            # install dev dependencies and create .env
#   scripts/setup.sh --no-deps  # only create .env and secrets
#   scripts/setup.sh --dev      # also set up a developer chat (see below)
#
# --dev generates ADMIN_MAGIC_STRING and asks for a fixed Korail account to
# put in USERID/USERPW. Sending that string to the bot turns the chat it was
# sent from into a developer chat: no trial limit, admin commands without a
# password, and logins with that account instead of a registered one. Only
# developer chats are affected, which is why the string has to be a real
# secret - so it is generated here rather than invented by hand.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

INSTALL_DEPS=1
DEV_SETUP=0
for arg in "$@"; do
    case "$arg" in
        --no-deps) INSTALL_DEPS=0 ;;
        --dev) DEV_SETUP=1 ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# ------------------------------------------------------- developer chat
if [[ "$DEV_SETUP" -eq 1 ]]; then
    echo
    info "개발자 채팅방 설정"

    # No terminal check: answers may legitimately be fed in from a file. An
    # input that runs out simply leaves the account unset, which is the same
    # as declining - and the magic string is generated either way, so --dev
    # still does something useful without a single answer.
    [[ -t 0 ]] || warn "터미널이 아닙니다. 답이 없으면 계정 저장은 건너뜁니다."

    EXISTING_MAGIC="$(env_value ADMIN_MAGIC_STRING)"
    if [[ -n "$EXISTING_MAGIC" ]]; then
        printf '  %s\n' "이미 설정된 개발자 문구가 있습니다."
        if ask_yn "새로 만들까요? (기존 개발자 방은 그대로 유지됩니다)" n; then
            MAGIC="$(gen_secret 24)"
        else
            MAGIC="$EXISTING_MAGIC"
        fi
    else
        MAGIC="$(gen_secret 24)"
    fi
    set_env_key ADMIN_MAGIC_STRING "$MAGIC"

    echo
    printf '  %s\n' "이 봇으로 개발·테스트할 때 쓸 코레일 계정을 넣어두면,"
    printf '  %s\n' "개발자 방에서는 로그인 단계를 건너뜁니다. 비워두면 개발자 방에서도"
    printf '  %s\n' "다른 사용자와 똑같이 계정을 등록해서 씁니다."
    echo

    if ask_yn "코레일 계정을 저장할까요?" y; then
        KORAIL_ID="$(ask "코레일 아이디 (휴대전화번호, 예: 010-1234-5678)" "$(clean_default "$(env_value USERID)")")"
        KORAIL_PW="$(ask_secret "코레일 비밀번호")"

        if [[ -n "$KORAIL_ID" && -n "$KORAIL_PW" ]]; then
            set_env_key USERID "$KORAIL_ID"
            set_env_key USERPW "$KORAIL_PW"
            ok "코레일 계정을 .env 에 저장했습니다."
        else
            warn "입력이 비어 있어 계정 저장은 건너뜁니다."
        fi
    fi

    echo
    ok "개발자 문구가 준비되었습니다."
    printf '%s\n' "  ────────────────────────────────────────────────"
    printf '  %s\n' "${C_YELLOW}${MAGIC}${C_RESET}"
    printf '%s\n' "  ────────────────────────────────────────────────"
    printf '  %s\n' "봇을 띄운 뒤 텔레그램에서 이 문구를 그대로 보내세요."
    printf '  %s\n' "보낸 채팅방이 개발자 방이 됩니다. 해제는 /devoff 입니다."
    printf '  %s\n' "${C_YELLOW}이 문구를 아는 사람은 누구나 개발자 방을 만들 수 있으니 공유하지 마세요.${C_RESET}"
fi

# ------------------------------------------------------------- dependencies
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
    require_uv

    # No interpreter search here: uv reads requires-python from
    # pyproject.toml and downloads that version when the host has not got it.
    info "Creating the environment from uv.lock (this can take a while)"
    if ! uv sync --frozen; then
        err "Dependency installation failed."
        err "If uv.lock is out of step with pyproject.toml, refresh it with:"
        die "  uv lock"
    fi
    ok "Dependencies installed into .venv ($(uv run --frozen python -V))"
else
    info "Skipping dependency installation (--no-deps)"
fi

# ------------------------------------------------------------------ summary
echo
ok "Setup complete."
echo
echo "Still to do by hand:"
echo "  1. Put your bot token in .env         -> BOTTOKEN="
echo "  2. Start the app                       -> scripts/run.sh"
echo "     (webhook mode only) register it     -> scripts/set-webhook.sh https://your.domain/telebot"
echo
if [[ "$DEV_SETUP" -eq 1 ]]; then
    echo "개발자 방:"
    echo "  봇을 띄운 뒤 위 문구를 텔레그램으로 보내면 그 방이 개발자 방이 됩니다."
else
    echo "Optional:"
    echo "  PREAPPROVED_USERS   승인 없이 바로 쓸 사람들의 전화번호 (비워두면 모두 체험부터)"
    echo "  TRIAL_SEARCH_LIMIT  승인 전 써볼 수 있는 검색 횟수 (기본 3)"
    echo "  scripts/setup.sh --dev  개발자 방 문구 생성 + 고정 코레일 계정 설정"
fi
