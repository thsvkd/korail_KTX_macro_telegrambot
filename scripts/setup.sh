#!/usr/bin/env bash
#
# Prepare a development environment: dependencies, .env, secrets.
#
# Usage:
#   scripts/setup.sh            # install dev dependencies and create .env
#   scripts/setup.sh --no-deps  # only create .env and secrets
#   scripts/setup.sh --dev      # also set up a developer chat (see below)
#   scripts/setup.sh --test     # create an isolated .env.test staging bot
#   scripts/setup.sh onboarding # guided setup from bot token to first reply
#   scripts/setup.sh secrets [--test] # generate or rotate selected secrets
#   scripts/setup.sh check [--test] # validate deployment settings
#
# --dev generates ADMIN_MAGIC_STRING and optionally stores fixed Korail/SRT
# accounts. Sending that string to the bot turns the chat it was
# sent from into a developer chat: no trial limit, admin commands without a
# password, and logins with that account instead of a registered one. Only
# developer chats are affected, which is why the string has to be a real
# secret - so it is generated here rather than invented by hand.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

setup_secrets() {

MANAGED_KEYS=(SESSION_SECRET ADMIN_PASSWORD REDIS_PASSWORD)

MODE="fill"
SECRET_TEST_RUNTIME=0
for arg in "$@"; do
    case "$arg" in
        --print) MODE="print" ;;
        --force) MODE="force" ;;
        --test) SECRET_TEST_RUNTIME=1 ;;
        -h|--help) printf '%s\n' 'Usage: scripts/setup.sh secrets [--test] [--print|--force]'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

(( SECRET_TEST_RUNTIME )) && use_test_stack

if [[ "$MODE" == "print" ]]; then
    for key in "${MANAGED_KEYS[@]}"; do
        printf '%s=%s\n' "$key" "$(gen_secret)"
    done
    exit 0
fi

require_env_file

# set_key <KEY> <VALUE> - replace the KEY= line in .env, appending if absent
set_key() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE"; then
        # Use a python pass so that values containing / & \ survive intact.
        KEY="$key" VALUE="$value" python3 - "$ENV_FILE" > "$tmp" <<'PY'
import os, sys

key = os.environ["KEY"]
value = os.environ["VALUE"]
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        if line.startswith(f"{key}="):
            print(f"{key}={value}")
        else:
            print(line, end="")
PY
        mv "$tmp" "$ENV_FILE"
    else
        rm -f "$tmp"
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

changed=0
for key in "${MANAGED_KEYS[@]}"; do
    current="$(env_value "$key")"
    if [[ -n "$current" && "$MODE" != "force" ]]; then
        info "$key is already set - leaving it alone (use --force to rotate)"
        continue
    fi
    set_key "$key" "$(gen_secret)"
    ok "Generated $key"
    changed=1
done

chmod 600 "$ENV_FILE"

if [[ "$changed" -eq 1 ]]; then
    echo
    warn "Restart the app so the new values take effect."
fi

ok "Secrets written to ${ENV_FILE#"$ROOT_DIR"/} (mode 600)"

}

setup_check() {

TEST_RUNTIME=0
PRODUCTION_ENV_FILE="$ENV_FILE"
for arg in "$@"; do
    case "$arg" in
        --test) TEST_RUNTIME=1 ;;
        -h|--help) printf '%s\n' 'Usage: scripts/setup.sh check [--test]'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done
(( TEST_RUNTIME )) && use_test_stack

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

FAILURES=0
WARNINGS=0

pass_check() { printf '  %s %s\n' "${C_GREEN}PASS${C_RESET}" "$1"; }
fail_check() { printf '  %s %s\n' "${C_RED}FAIL${C_RESET}" "$1"; FAILURES=$((FAILURES + 1)); }
warn_check() { printf '  %s %s\n' "${C_YELLOW}WARN${C_RESET}" "$1"; WARNINGS=$((WARNINGS + 1)); }

# is_placeholder <value> - true for the sample values shipped in .env.example
is_placeholder() {
    [[ "$1" =~ ^your_.*_here$ ]]
}

# check_required <KEY> - report a secret the app refuses to start without
check_required() {
    local key="$1"
    local value
    value="$(env_value "$key")"

    if is_placeholder "$value"; then
        fail_check "${key} still holds the .env.example placeholder"
    elif [[ -n "$value" ]]; then
        pass_check "${key} is set"
    else
        fail_check "${key} is empty - the app will refuse to start"
    fi
}

echo "Configuration"
ENV_LABEL="${ENV_FILE#"$ROOT_DIR"/}"

if [[ -f "$ENV_FILE" ]]; then
    pass_check "${ENV_LABEL} exists"

    perms="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%A' "$ENV_FILE" 2>/dev/null || echo '???')"
    if [[ "$perms" == "600" || "$perms" == "400" ]]; then
        pass_check "${ENV_LABEL} permissions are ${perms}"
    else
        warn_check "${ENV_LABEL} permissions are ${perms} - tighten with 'chmod 600 ${ENV_LABEL}'"
    fi

    check_required BOTTOKEN

    pass_check "Telegram updates use long polling - no public endpoint is exposed"

    # Telegram tokens look like <bot_id>:<35 chars>. Catching a mangled one
    # here beats discovering it when the first message fails to send.
    bottoken="$(env_value BOTTOKEN)"
    if [[ -n "$bottoken" ]] && ! is_placeholder "$bottoken"; then
        if [[ "$bottoken" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            pass_check "BOTTOKEN has the expected <bot_id>:<token> shape"
        else
            fail_check "BOTTOKEN is malformed - expected <digits>:<token>"
        fi
    fi

    for key in SESSION_SECRET REDIS_PASSWORD; do
        if [[ -n "$(env_value "$key")" ]]; then
            pass_check "${key} is set"
        else
            fail_check "${key} is empty - generate it in ${ENV_LABEL}"
        fi
    done

    # Resuming a search means reading back a password stored before the
    # restart, which an ephemeral key cannot do - the feature would fail
    # silently exactly when it is needed.
    resume_on_restart="$(env_value RESUME_ON_RESTART)"
    resume_on_restart="${resume_on_restart:-true}"
    if [[ "$resume_on_restart" =~ ^([Tt]rue|1|[Yy]es|[Oo]n)$ ]]; then
        if [[ -n "$(env_value SESSION_SECRET)" ]]; then
            pass_check "RESUME_ON_RESTART is on and SESSION_SECRET can decrypt it"
        else
            fail_check "RESUME_ON_RESTART is on but SESSION_SECRET is empty - interrupted searches can never be resumed"
        fi
    else
        pass_check "RESUME_ON_RESTART is off - no credentials are kept between restarts"
    fi

    if [[ -n "$(env_value ADMIN_PASSWORD)" ]]; then
        pass_check "ADMIN_PASSWORD is set"
        if [[ -n "$(env_value USERPW)" && "$(env_value ADMIN_PASSWORD)" == "$(env_value USERPW)" ]]; then
            fail_check "ADMIN_PASSWORD equals USERPW - guessing it would expose the Korail account"
        else
            pass_check "ADMIN_PASSWORD differs from USERPW"
        fi
    else
        warn_check "ADMIN_PASSWORD is empty - admin commands are disabled"
    fi

    # The sample list locks the owner out of their own bot with a confusing
    # "권한이 없습니다", which is not obviously a config problem.
    allow_list="$(env_value ALLOW_LIST)"
    trial_limit="$(env_value TRIAL_SEARCH_LIMIT)"
    if [[ -z "$allow_list" && "$TEST_RUNTIME" -eq 1 && "$trial_limit" == "0" ]]; then
        pass_check "TRIAL_SEARCH_LIMIT=0 - non-developers cannot start test searches"
    elif [[ -z "$allow_list" ]]; then
        warn_check "ALLOW_LIST is empty - anyone who finds the bot can use its trial"
    elif [[ "$allow_list" == *"010-1234-5678"* || "$allow_list" == *"010-9876-5432"* ]]; then
        fail_check "ALLOW_LIST still holds the .env.example sample numbers - real users will be refused"
    else
        pass_check "ALLOW_LIST is configured"
    fi

    debug_value="$(env_value FLASK_DEBUG)"
    if [[ -z "$debug_value" || "$debug_value" =~ ^([Ff]alse|0|[Nn]o|[Oo]ff)$ ]]; then
        pass_check "FLASK_DEBUG is off"
    else
        fail_check "FLASK_DEBUG=${debug_value} - the Werkzeug debugger allows remote code execution"
    fi
else
    setup_option=""
    (( TEST_RUNTIME )) && setup_option=" --test"
    fail_check "${ENV_LABEL} is missing - run 'scripts/setup.sh${setup_option}'"
fi

if (( TEST_RUNTIME )) && [[ -f "$PRODUCTION_ENV_FILE" && -f "$ENV_FILE" ]]; then
    production_token="$(sed -n 's/^BOTTOKEN=//p' "$PRODUCTION_ENV_FILE" | tail -n 1)"
    test_token="$(env_value BOTTOKEN)"
    if [[ -n "$production_token" && "$test_token" == "$production_token" ]]; then
        fail_check "${ENV_LABEL} reuses the production BOTTOKEN"
    else
        pass_check "test and production BOTTOKEN values are distinct"
    fi
fi

echo
echo "Repository"

if git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    if git -C "$ROOT_DIR" ls-files --error-unmatch "$ENV_LABEL" >/dev/null 2>&1; then
        fail_check "${ENV_LABEL} is tracked by git - remove it from the index immediately"
    else
        pass_check "${ENV_LABEL} is not tracked by git"
    fi

    if git -C "$ROOT_DIR" check-ignore -q "$ENV_LABEL" 2>/dev/null; then
        pass_check "${ENV_LABEL} is covered by .gitignore"
    else
        warn_check "${ENV_LABEL} is not covered by .gitignore"
    fi
else
    warn_check "Not a git repository - skipping repository checks"
fi

echo
echo "Deployment"

if grep -qE '^\s*-\s*"6379:6379"' "${ROOT_DIR}/docker-compose.yml"; then
    fail_check "docker-compose publishes Redis on the host (port 6379)"
else
    pass_check "Redis is not published to the host"
fi

if grep -q 'requirepass' "${ROOT_DIR}/docker-compose.yml"; then
    pass_check "Redis requires a password"
else
    fail_check "Redis starts without authentication"
fi

if grep -qE '^\s*uses:.*@(master|main)\s*$' "${ROOT_DIR}/.github/workflows/cicd.yml" 2>/dev/null; then
    warn_check "A GitHub Action is pinned to @master - pin it to a release tag"
else
    pass_check "GitHub Actions are pinned to releases"
fi

echo
if [[ "$FAILURES" -gt 0 ]]; then
    err "${FAILURES} failed check(s), ${WARNINGS} warning(s)"
    exit 1
fi

if [[ "$WARNINGS" -gt 0 ]]; then
    warn "All critical checks passed, ${WARNINGS} warning(s)"
    exit 0
fi

ok "All checks passed"

}

setup_onboarding() {

TOTAL_STEPS=8
RESET=0

for arg in "$@"; do
    case "$arg" in
        --reset) RESET=1 ;;
        -h|--help) printf '%s\n' 'Usage: scripts/setup.sh onboarding [--reset]'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

# ---------------------------------------------------------------- 표시 헬퍼

hr() { printf '%s\n' "────────────────────────────────────────────────────────"; }

step() {
    echo
    printf '%s\n' "${C_BLUE}━━━ [$1/${TOTAL_STEPS}] $2 ${C_RESET}"
    echo
}

say()  { printf '  %s\n' "$*"; }
note() { printf '  %s %s\n' "${C_YELLOW}!${C_RESET}" "$*"; }


# clean_default <value> - drop .env.example placeholders so that pressing
# Enter never stores a sample value as if it were real
# ask_choice <prompt> <default_index> <label...> - echoes the chosen index
ask_choice() {
    local prompt="$1" default="$2"; shift 2
    local labels=("$@") answer index
    printf '  %s\n' "$prompt" >&2
    for index in "${!labels[@]}"; do
        printf '    %d) %s\n' "$((index + 1))" "${labels[$index]}" >&2
    done
    while true; do
        printf '  선택 [%s]: ' "$default" >&2
        read -r answer
        answer="${answer:-$default}"
        if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= ${#labels[@]} )); then
            printf '%s' "$answer"
            return 0
        fi
        printf '  %s\n' "1~${#labels[@]} 중에서 골라주세요." >&2
    done
}

# ------------------------------------------------------------------- 시작

clear 2>/dev/null || true
hr
echo "  🚄 코레일 KTX 예매 봇 — 설정 마법사"
hr
echo
say "매진된 열차를 계속 지켜보다가 취소표가 나오면 자동으로 잡아주는 봇입니다."
say "설정이 끝나면 이후 예매는 전부 텔레그램 대화로 진행합니다."
echo
say "중간에 언제든 Ctrl-C로 중단할 수 있고, 다시 실행하면 이어서 진행됩니다."
echo

if [[ -f "$ENV_FILE" && "$RESET" -eq 0 ]]; then
    note "이미 .env 설정이 있습니다. 값을 하나씩 확인하며 진행합니다."
    note "처음부터 새로 하시려면 Ctrl-C 후 'scripts/setup.sh onboarding --reset'을 실행하세요."
    echo
fi

if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$ENV_EXAMPLE" ]] || die ".env.example이 없습니다. 저장소가 온전한지 확인해주세요."
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok ".env 파일을 만들었습니다."
fi

# =================================================== [1] 환경 점검

step 1 "환경 점검"

HAS_DOCKER=0
DOCKER_NOTE=""

if command -v python3 >/dev/null 2>&1; then
    say "✔ python3 ($(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'))"
else
    die "python3가 필요합니다. 설치 후 다시 실행해주세요."
fi

if ! command -v docker >/dev/null 2>&1; then
    DOCKER_NOTE="docker가 설치되어 있지 않습니다"
elif ! docker info >/dev/null 2>&1; then
    DOCKER_NOTE="docker 데몬에 접근할 수 없습니다 (docker 그룹에 속해 있는지 확인하세요)"
elif ! has_compose; then
    # docker alone is not enough: the stack is defined as a compose project.
    DOCKER_NOTE="docker는 있지만 Docker Compose가 없습니다"
    say "  ${C_YELLOW}설치 (권장, sudo 불필요):${C_RESET}"
    say "    mkdir -p ~/.docker/cli-plugins"
    say "    curl -SL \"https://github.com/docker/compose/releases/latest/download/docker-compose-linux-\$(uname -m)\" \\"
    say "      -o ~/.docker/cli-plugins/docker-compose"
    say "    chmod +x ~/.docker/cli-plugins/docker-compose"
    say "  설치 후 이 스크립트를 다시 실행하면 Docker로 진행할 수 있습니다."
else
    HAS_DOCKER=1
    say "✔ docker + compose 사용 가능 (compose $(compose_version))"
    if command -v systemctl >/dev/null 2>&1; then
        if [[ "$(systemctl is-enabled docker 2>/dev/null)" == "enabled" ]]; then
            say "✔ docker 데몬이 부팅 시 자동 시작됩니다 (재부팅 후에도 봇이 살아납니다)"
        else
            note "docker 데몬이 부팅 시 자동 시작되지 않습니다."
            note "  재부팅 후에도 봇을 돌리려면: sudo systemctl enable docker"
        fi
    fi
fi

[[ -n "$DOCKER_NOTE" ]] && note "$DOCKER_NOTE"

# CI publishes linux/amd64 and linux/arm64, but any tag pushed before that
# change is amd64-only. Building locally works on either, so non-x86_64 hosts
# keep doing that rather than depending on which tag is currently up.
ARCH="$(uname -m)"
say "✔ 아키텍처: ${ARCH}"
if [[ "$ARCH" != "x86_64" && "$HAS_DOCKER" -eq 1 ]]; then
    note "이 기기에서는 이미지를 직접 빌드합니다."
    note "  (뒤에서 자동으로 처리합니다)"
fi

# =================================================== [2] 사용 형태

step 2 "사용 형태"

USAGE_CHOICE="$(ask_choice "이 봇을 누가 사용하나요?" 1 \
    "나 혼자 사용합니다 (권장)" \
    "여러 명이 함께 사용합니다")"

if [[ "$USAGE_CHOICE" == "1" ]]; then
    USAGE_MODE="solo"
    say "→ 혼자 사용. 코레일 로그인을 간편하게 설정해 드립니다."
else
    USAGE_MODE="multi"
    say "→ 여러 명 사용. 각자 텔레그램에서 코레일 정보를 입력하게 됩니다."
fi

echo
say "텔레그램 메시지는 long polling으로 받습니다."
say "→ 공개 주소·HTTPS·공유기 포트포워딩 없이 동작합니다."

# =================================================== [3] 봇 토큰

step 3 "텔레그램 봇 토큰"

CURRENT_TOKEN="$(env_value BOTTOKEN)"
BOT_USERNAME=""

# verify_token <token> - prints "@username" when Telegram accepts it
verify_token() {
    local token="$1" response
    response="$(curl -sS --max-time 15 "https://api.telegram.org/bot${token}/getMe" 2>/dev/null || true)"
    printf '%s' "$response" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not data.get("ok"):
    sys.exit(1)
print(data["result"].get("username", ""))
' 2>/dev/null
}

if [[ -n "$CURRENT_TOKEN" && ! "$CURRENT_TOKEN" =~ ^your_.*_here$ ]]; then
    say "기존 토큰이 있습니다. 확인 중..."
    if BOT_USERNAME="$(verify_token "$CURRENT_TOKEN")" && [[ -n "$BOT_USERNAME" ]]; then
        ok "@${BOT_USERNAME} 확인됨"
        if ! ask_yn "이 봇을 계속 사용하시겠어요?" y; then
            CURRENT_TOKEN=""
            BOT_USERNAME=""
        fi
    else
        note "기존 토큰이 유효하지 않습니다. 새로 입력해주세요."
        CURRENT_TOKEN=""
    fi
fi

if [[ -z "$BOT_USERNAME" ]]; then
    echo
    say "봇 토큰 발급 방법:"
    say "  1. 텔레그램에서 @BotFather 를 검색해 대화를 엽니다"
    say "  2. /newbot 을 입력합니다"
    say "  3. 봇 이름과 아이디(_bot 으로 끝나야 함)를 정합니다"
    say "  4. 받은 토큰을 아래에 붙여넣습니다"
    echo
    say "토큰은 '123456789:AAH...' 형태입니다."
    echo

    while true; do
        TOKEN="$(ask_secret "봇 토큰")"

        if [[ -z "$TOKEN" ]]; then
            note "토큰을 입력해주세요."
            continue
        fi

        if [[ ! "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            note "형식이 올바르지 않습니다. <숫자>:<문자열> 형태여야 합니다."
            continue
        fi

        say "텔레그램에 확인 중..."
        if BOT_USERNAME="$(verify_token "$TOKEN")" && [[ -n "$BOT_USERNAME" ]]; then
            set_env_key BOTTOKEN "$TOKEN"
            ok "@${BOT_USERNAME} 연결 확인!"
            break
        fi

        note "텔레그램이 이 토큰을 거부했습니다. 복사가 잘못되었는지 확인해주세요."
    done
fi

# =================================================== [4] 시크릿 생성

step 4 "보안 키 생성"

say "세션 암호화 키, Redis 비밀번호 등을 자동으로 만듭니다."
echo
setup_secrets || die "시크릿 생성에 실패했습니다."

# Auto-resume needs the Korail password to survive a restart, which is only
# possible while SESSION_SECRET can decrypt it.
echo
say "재부팅이나 재시작으로 검색이 끊겼을 때 자동으로 이어서 검색할 수 있습니다."
say "이 기능을 쓰면 검색이 진행되는 동안 코레일 비밀번호가 암호화되어 보관됩니다."
say "(검색이 끝나거나 취소되면 즉시 삭제됩니다)"
echo

if ask_yn "재시작 후 자동으로 검색을 이어갈까요?" y; then
    set_env_key RESUME_ON_RESTART "true"
    say "→ 자동 재개를 켭니다."
else
    set_env_key RESUME_ON_RESTART "false"
    say "→ 재시작 시 알림만 보내고 검색은 중단됩니다."
fi

# =================================================== [5] 실행 방식

step 5 "실행 방식"

RUN_MODE="local"

if [[ "$HAS_DOCKER" -eq 1 ]]; then
    RUN_CHOICE="$(ask_choice "어떻게 실행할까요?" 1 \
        "Docker (권장 - 재부팅 후 자동 복구, Redis 포함)" \
        "직접 실행 (uv - 개발용)")"
    [[ "$RUN_CHOICE" == "1" ]] && RUN_MODE="docker"
else
    note "docker를 쓸 수 없어 직접 실행으로 진행합니다."
fi

if [[ "$RUN_MODE" == "docker" ]]; then
    OVERRIDE_FILE="${ROOT_DIR}/docker-compose.override.yml"

    # docker-compose.yml pulls the published image, which lags this checkout.
    # A local override builds from the working tree instead. CI copies only
    # docker-compose.yml, so this stays local.
    {
        echo "# 온보딩이 생성한 로컬 개발용 오버라이드입니다."
        echo "# docker-compose.yml은 배포된 이미지를 받아 쓰지만, 여기서는"
        echo "# 이 저장소 소스로 직접 빌드합니다."
        echo "services:"
        echo "  app:"
        echo "    build: ."
        echo "    image: korailbot:local"
        # Compose appends sequences when merging, so dropping the inherited
        # port mapping needs the !reset tag (Compose v2.24+).
        if compose_supports_reset; then
            echo "    # Long polling은 외부에서 들어올 요청이 없습니다."
            echo "    ports: !reset []"
        fi
    } > "$OVERRIDE_FILE"

    ok "docker-compose.override.yml 생성 완료"
    if compose_supports_reset; then
        say "  Long polling이라 외부에 공개하는 포트가 없습니다."
    else
        note "Long polling에서는 8000 포트를 공개할 필요가 없지만,"
        note "  이 Compose 버전(≥2.24 필요)에서는 자동으로 제거할 수 없습니다."
        note "  원하시면 docker-compose.yml의 app.ports 항목을 지우세요."
    fi

    # Docker's default REDIS_HOST is the compose service name.
    set_env_key REDIS_HOST "redis"

    echo
    note "첫 빌드는 라즈베리파이에서 10분 이상 걸릴 수 있습니다."
    if ask_yn "지금 이미지를 빌드할까요?" y; then
        say "빌드 중... (로그가 길게 출력됩니다)"
        if compose build; then
            ok "이미지 빌드 완료"
        else
            note "빌드에 실패했습니다. 나중에 'docker compose build'로 다시 시도하세요."
        fi
    else
        say "→ 나중에 'docker compose build'로 빌드하세요."
    fi
else
    set_env_key REDIS_HOST "localhost"
    echo
    if ask_yn "파이썬 의존성을 지금 설치할까요? (scripts/setup.sh)" y; then
        "${SCRIPT_DIR}/setup.sh" || note "의존성 설치에 실패했습니다. 나중에 다시 시도하세요."
    fi
fi

# =================================================== [6] 철도 계정

step 6 "코레일·SRT 계정"

if [[ "$USAGE_MODE" == "solo" ]]; then
    say "혼자 사용하실 때는 자주 쓰는 철도 계정을 여기에 저장해두는 편이 낫습니다."
    say "매번 채팅에 비밀번호를 입력하지 않아도 되고, 무엇보다"
    say "${C_YELLOW}비밀번호가 텔레그램 대화 기록에 남지 않습니다.${C_RESET}"
    echo
    say "저장한 계정은 ${C_YELLOW}개발자 방에서만${C_RESET} 쓰입니다. 아래에서 만들어 드리는"
    say "문구를 텔레그램으로 보내면 그 채팅방이 개발자 방이 됩니다."
    echo

    STORED_FIXED_ACCOUNT=0
    if ask_yn "코레일 계정을 저장할까요?" y; then
        KORAIL_ID="$(ask "코레일 회원번호 (예: 010-1234-5678)" "$(clean_default "$(env_value USERID)")")"
        KORAIL_PW="$(ask_secret "코레일 비밀번호")"

        if [[ -n "$KORAIL_ID" && -n "$KORAIL_PW" ]]; then
            set_env_key USERID "$KORAIL_ID"
            set_env_key USERPW "$KORAIL_PW"
            STORED_FIXED_ACCOUNT=1
        else
            note "입력이 비어 있어 건너뜁니다."
        fi
    else
        say "→ 매번 채팅에서 코레일 정보를 입력하게 됩니다."
    fi

    if ask_yn "SRT 계정을 저장할까요?" n; then
        SRT_ACCOUNT_ID="$(ask "SRT 회원번호·휴대전화번호·이메일" "$(clean_default "$(env_value SRT_ID)")")"
        SRT_ACCOUNT_PW="$(ask_secret "SRT 비밀번호")"

        if [[ -n "$SRT_ACCOUNT_ID" && -n "$SRT_ACCOUNT_PW" ]]; then
            set_env_key SRT_ID "$SRT_ACCOUNT_ID"
            set_env_key SRT_PW "$SRT_ACCOUNT_PW"
            STORED_FIXED_ACCOUNT=1
        else
            note "입력이 비어 있어 건너뜁니다."
        fi
    fi

    if (( STORED_FIXED_ACCOUNT )); then
        # 이 문구를 아는 사람은 누구나 개발자 방을 만들 수 있으므로 직접
        # 정하게 두지 않고 충분히 긴 무작위 값을 만든다.
        MAGIC="$(env_value ADMIN_MAGIC_STRING)"
        [[ -n "$MAGIC" ]] || MAGIC="$(gen_secret 24)"
        set_env_key ADMIN_MAGIC_STRING "$MAGIC"

        echo
        ok "설정 완료. 봇을 띄운 뒤 아래 문구를 텔레그램으로 보내세요."
        say "${C_YELLOW}${MAGIC}${C_RESET}"
        say "보낸 채팅방이 개발자 방이 되고, 선택한 철도의 고정 계정을 사용합니다."
        say "해제는 /devoff, 공유는 금물입니다."
    fi
else
    say "여러 명이 사용하므로 각자 텔레그램에서 철도 계정을 등록합니다."
    say "서버 고정 계정은 사용하지 않습니다."
    # A magic string in a shared bot would let any user log in as the owner.
    set_env_key ADMIN_MAGIC_STRING ""
    set_env_key USERID ""
    set_env_key USERPW ""
    set_env_key SRT_ID ""
    set_env_key SRT_PW ""
fi

# =================================================== [7] 접근 제어

step 7 "사용자 제한"

say "봇을 아무나 쓰지 못하게 전화번호로 제한할 수 있습니다."
say "비워두면 봇 주소를 아는 누구나 사용할 수 있습니다."
echo

CURRENT_ALLOW="$(env_value ALLOW_LIST)"
[[ "$CURRENT_ALLOW" == *"010-1234-5678"* ]] && CURRENT_ALLOW=""
DEFAULT_ALLOW="${CURRENT_ALLOW:-${KORAIL_ID:-}}"

ALLOW_INPUT="$(ask "허용할 전화번호 (쉼표로 구분, 비우면 제한 없음)" "$DEFAULT_ALLOW")"
set_env_key ALLOW_LIST "$ALLOW_INPUT"

if [[ -z "$ALLOW_INPUT" ]]; then
    note "제한 없음으로 설정했습니다. 봇 주소가 알려지면 누구나 사용할 수 있습니다."
else
    ok "허용 목록: ${ALLOW_INPUT}"
fi

# =================================================== [8] 연결 확인

step 8 "연결 확인"

TOKEN="$(env_value BOTTOKEN)"
CHAT_ID=""

# getUpdates has exactly one consumer per token, so a running bot would fight
# this check for the same updates.
if [[ -n "$(bot_pids)" ]]; then
    note "봇이 이미 실행 중이라 연결 확인을 건너뜁니다."
    note "  확인하시려면 봇을 멈춘 뒤 이 단계를 다시 실행하세요."
elif ask_yn "지금 실제로 메시지를 주고받아 확인해볼까요?" y; then
    echo
    say "텔레그램에서 ${C_GREEN}@${BOT_USERNAME}${C_RESET} 을 열고"
    say "아무 메시지나 하나 보내주세요. (예: 안녕)"
    echo
    say "기다리는 중... (최대 2분, Ctrl-C로 건너뛰기)"

    DEADLINE=$((SECONDS + 120))
    while (( SECONDS < DEADLINE )); do
        RESPONSE="$(curl -sS --max-time 35 \
            "https://api.telegram.org/bot${TOKEN}/getUpdates?timeout=25&offset=-1" \
            2>/dev/null || true)"

        RESULT="$(printf '%s' "$RESPONSE" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if not data.get("ok"):
    if data.get("error_code") == 409:
        print("CONFLICT")
    sys.exit(0)

for update in reversed(data.get("result", [])):
    message = update.get("message")
    if message and "chat" in message:
        chat = message["chat"]
        name = chat.get("first_name") or chat.get("title") or "사용자"
        print(f"{chat[\"id\"]}\t{name}")
        break
' 2>/dev/null || true)"

        if [[ "$RESULT" == "CONFLICT" ]]; then
            note "다른 곳에서 이 봇이 이미 실행 중입니다. 그쪽을 멈춘 뒤 다시 시도하세요."
            break
        fi

        if [[ -n "$RESULT" ]]; then
            CHAT_ID="${RESULT%%$'\t'*}"
            SENDER="${RESULT#*$'\t'}"
            ok "${SENDER}님의 메시지를 받았습니다!"

            curl -sS --max-time 15 -X POST \
                "https://api.telegram.org/bot${TOKEN}/sendMessage" \
                --data-urlencode "chat_id=${CHAT_ID}" \
                --data-urlencode "text=✅ 설정이 확인되었습니다! 이제 /start 로 예매를 시작할 수 있습니다." \
                >/dev/null 2>&1 || true

            ok "답장을 보냈습니다. 텔레그램을 확인해보세요."
            break
        fi
    done

    [[ -z "$CHAT_ID" ]] && note "메시지를 받지 못했습니다. 나중에 다시 확인해도 됩니다."
fi

# =================================================== 마무리

echo
hr
echo "  설정 점검"
hr
echo
setup_check || true

echo
hr
echo "  ✅ 설정 완료"
hr
echo
say "실행 방법:"
if [[ "$RUN_MODE" == "docker" ]]; then
    say "  docker compose up -d          # 시작 (재부팅 후 자동 복구)"
    say "  scripts/deploy.sh logs        # 로그 보기"
    say "  docker compose down           # 중지"
else
    say "  scripts/run.sh                # Redis 자동 준비 후 봇 시작"
fi

echo
say "예매하는 방법:"
say "  1. 텔레그램에서 @${BOT_USERNAME} 열기"
MAGIC_VALUE="$(env_value ADMIN_MAGIC_STRING)"
if [[ -n "$MAGIC_VALUE" ]]; then
    say "  2. '${MAGIC_VALUE}' 입력해 개발자 방 활성화"
    say "  3. /start → '예' → 코레일 또는 SRT 선택"
else
    say "  2. /start → '예' → 코레일 또는 SRT 선택"
    say "  3. 선택한 철도의 회원번호와 비밀번호 입력"
fi
say "  4. 날짜(20260815) → 출발역 → 도착역 순으로 답하기"
say "  5. 시간대·인원까지 답하면 검색이 시작됩니다"
echo
say "취소표가 나오면 자동으로 예약하고 알려드립니다."
note "예약 후 ${C_YELLOW}10분 안에 선택한 철도사에서 직접 결제${C_RESET}해야 취소되지 않습니다."
echo
say "  /status  진행 상황 확인"
say "  /cancel  검색 중단"
echo

}

case "${1:-}" in
    secrets)
        shift
        setup_secrets "$@"
        exit
        ;;
    check)
        shift
        setup_check "$@"
        exit
        ;;
    onboarding)
        shift
        setup_onboarding "$@"
        exit
        ;;
esac


INSTALL_DEPS=1
DEV_SETUP=0
TEST_SETUP=0
for arg in "$@"; do
    case "$arg" in
        --no-deps) INSTALL_DEPS=0 ;;
        --dev) DEV_SETUP=1 ;;
        --test) TEST_SETUP=1 ;;
        -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

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
setup_secrets

# configure_developer_chat <label>
#
# Works against whichever file ENV_FILE currently names. The test-bot setup
# points it at .env.test; ordinary --dev leaves it on .env.
configure_developer_chat() {
    local label="$1" existing_magic magic korail_id korail_pw srt_id srt_pw

    echo
    info "${label} 개발자 채팅방 설정"

    # No terminal check: answers may legitimately be fed in from a file. An
    # input that runs out simply leaves the account unset, which is the same
    # as declining - and the magic string is generated either way, so --dev
    # still does something useful without a single answer.
    [[ -t 0 ]] || warn "터미널이 아닙니다. 답이 없으면 계정 저장은 건너뜁니다."

    existing_magic="$(env_value ADMIN_MAGIC_STRING)"
    if [[ -n "$existing_magic" ]]; then
        printf '  %s\n' "이미 설정된 개발자 문구가 있습니다."
        if ask_yn "새로 만들까요? (기존 개발자 방은 그대로 유지됩니다)" n; then
            magic="$(gen_secret 24)"
        else
            magic="$existing_magic"
        fi
    else
        magic="$(gen_secret 24)"
    fi
    set_env_key ADMIN_MAGIC_STRING "$magic"

    echo
    printf '  %s\n' "이 봇으로 개발·테스트할 때 쓸 코레일 계정을 넣어두면,"
    printf '  %s\n' "개발자 방에서는 로그인 단계를 건너뜁니다. 비워두면 개발자 방에서도"
    printf '  %s\n' "다른 사용자와 똑같이 계정을 등록해서 씁니다."
    echo

    if ask_yn "코레일 계정을 저장할까요?" y; then
        korail_id="$(ask "코레일 아이디 (휴대전화번호, 예: 010-1234-5678)" "$(clean_default "$(env_value USERID)")")"
        korail_pw="$(ask_secret "코레일 비밀번호")"

        if [[ -n "$korail_id" && -n "$korail_pw" ]]; then
            set_env_key USERID "$korail_id"
            set_env_key USERPW "$korail_pw"
            ok "코레일 계정을 ${ENV_FILE#"$ROOT_DIR"/} 에 저장했습니다."
        else
            warn "입력이 비어 있어 계정 저장은 건너뜁니다."
        fi
    fi

    if ask_yn "SRT 계정을 저장할까요?" n; then
        srt_id="$(ask "SRT 아이디 (회원번호·휴대전화번호·이메일)" "$(clean_default "$(env_value SRT_ID)")")"
        srt_pw="$(ask_secret "SRT 비밀번호")"

        if [[ -n "$srt_id" && -n "$srt_pw" ]]; then
            set_env_key SRT_ID "$srt_id"
            set_env_key SRT_PW "$srt_pw"
            ok "SRT 계정을 ${ENV_FILE#"$ROOT_DIR"/} 에 저장했습니다."
        else
            warn "입력이 비어 있어 SRT 계정 저장은 건너뜁니다."
        fi
    fi

    echo
    ok "개발자 문구가 준비되었습니다."
    printf '%s\n' "  ────────────────────────────────────────────────"
    printf '  %s\n' "${C_YELLOW}${magic}${C_RESET}"
    printf '%s\n' "  ────────────────────────────────────────────────"
    printf '  %s\n' "봇을 띄운 뒤 텔레그램에서 이 문구를 그대로 보내세요."
    printf '  %s\n' "보낸 채팅방이 개발자 방이 됩니다. 해제는 /devoff 입니다."
    printf '  %s\n' "${C_YELLOW}이 문구를 아는 사람은 누구나 개발자 방을 만들 수 있으니 공유하지 마세요.${C_RESET}"
}

# ------------------------------------------------------- developer chat
if [[ "$DEV_SETUP" -eq 1 ]]; then
    configure_developer_chat "운영 봇"
fi

# ----------------------------------------------------------- test bot
if [[ "$TEST_SETUP" -eq 1 ]]; then
    production_env="$ENV_FILE"
    production_token="$(clean_default "$(env_value BOTTOKEN)")"
    production_port="$(clean_default "$(env_value FLASK_PORT)")"
    production_port="${production_port:-8080}"

    echo
    info "격리된 테스트 봇 설정"

    if [[ -f "$TEST_ENV_FILE" ]]; then
        info "${TEST_ENV_FILE#"$ROOT_DIR"/} already exists - keeping it"
    else
        cp "$ENV_EXAMPLE" "$TEST_ENV_FILE"
        chmod 600 "$TEST_ENV_FILE"
        ok "Created ${TEST_ENV_FILE#"$ROOT_DIR"/} from .env.example"
    fi

    ENV_FILE="$TEST_ENV_FILE"
    export ENV_FILE

    # Compose interpolation, service env, containers, port and project all
    # get names of their own. The project name is what gives Redis its own
    # network and named volume rather than sharing production state.
    set_env_key COMPOSE_PROJECT_NAME "korail-bot-test"
    set_env_key APP_CONTAINER_NAME "korail_bot_test"
    set_env_key REDIS_CONTAINER_NAME "korail_redis_test"
    set_env_key DEV_REDIS_CONTAINER_NAME "korail_test_dev_redis"
    set_env_key BOT_ENV_FILE "${TEST_ENV_FILE#"$ROOT_DIR"/}"
    set_env_key BOT_RUNTIME_PROFILE "test"
    set_env_key TRIAL_SEARCH_LIMIT "0"
    set_env_key MAX_CONCURRENT_SEARCHES "1"
    set_env_key RESUME_ON_RESTART "false"
    set_env_key DEV_REDIS_PORT "6380"

    current_test_port="$(clean_default "$(env_value FLASK_PORT)")"
    [[ "$current_test_port" == "$production_port" ]] && current_test_port=""
    test_port="$(ask "테스트 봇 내부 HTTP 포트" "$current_test_port")"
    test_port="${test_port:-8081}"
    if ! [[ "$test_port" =~ ^[0-9]+$ ]] || \
        (( test_port < 1 || test_port > 65535 )); then
        die "테스트 봇 포트는 1~65535 숫자여야 합니다."
    fi
    [[ "$test_port" != "$production_port" ]] || \
        die "${test_port}은 운영 봇 포트입니다. 테스트 봇에는 다른 포트를 쓰세요."
    set_env_key FLASK_PORT "$test_port"

    test_image="$(ask "테스트 이미지 태그" "$(clean_default "$(env_value IMAGE_NAME)")")"
    test_image="${test_image:-korailbot:test}"
    set_env_key IMAGE_NAME "$test_image"

    test_token="$(ask "BotFather에서 만든 테스트 봇 토큰" "$(clean_default "$(env_value BOTTOKEN)")")"
    if [[ -n "$production_token" && "$test_token" == "$production_token" ]]; then
        warn "운영 봇과 같은 토큰은 사용할 수 없습니다. BOTTOKEN을 비워둡니다."
        test_token=""
    fi
    set_env_key BOTTOKEN "$test_token"

    info "Generating missing test-bot secrets"
    setup_secrets
    configure_developer_chat "테스트 봇"

    ENV_FILE="$production_env"
    export ENV_FILE

    echo
    ok "격리된 테스트 봇 설정이 준비되었습니다."
    echo "  이미지 빌드: scripts/deploy.sh --test build"
    echo "  테스트 봇 기동: scripts/deploy.sh --test up"
    echo "  호스트 데몬: scripts/run.sh --test --daemon  # 전용 Redis도 자동 기동"
    echo "  로그 확인: scripts/deploy.sh --test logs"
    echo "  테스트 봇 중지: scripts/deploy.sh --test down"
    echo "  설정 점검: scripts/setup.sh check --test"
    if [[ -z "$test_token" ]]; then
        warn "기동 전에 .env.test 의 BOTTOKEN에 별도 테스트 봇 토큰을 넣으세요."
    fi
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
echo
if [[ "$DEV_SETUP" -eq 1 ]]; then
    echo "개발자 방:"
    echo "  봇을 띄운 뒤 위 문구를 텔레그램으로 보내면 그 방이 개발자 방이 됩니다."
else
    echo "Optional:"
    echo "  PREAPPROVED_USERS   승인 없이 바로 쓸 사람들의 전화번호 (비워두면 모두 체험부터)"
    echo "  TRIAL_SEARCH_LIMIT  승인 전 써볼 수 있는 검색 횟수 (기본 3)"
    echo "  scripts/setup.sh --dev  개발자 방 문구 생성 + 고정 철도 계정 설정"
fi
if [[ "$TEST_SETUP" -eq 1 ]]; then
    echo
    echo "테스트 봇:"
    echo "  Compose: scripts/deploy.sh --test build && scripts/deploy.sh --test up"
    echo "  호스트: scripts/run.sh --test --daemon  # 전용 Redis도 자동 기동"
    echo "  운영 봇과 다른 Telegram 토큰·Redis 볼륨·포트를 사용합니다."
fi
