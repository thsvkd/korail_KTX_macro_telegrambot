#!/usr/bin/env bash
#
# Move existing Redis state into the compose stack's data directory.
#
# The bot used to run on the host against a standalone Redis container, and
# before that the stack kept its data in a named volume. Both hold the same
# thing - registered railway accounts, sessions, searches waiting to start -
# and neither is where docker-compose.yml now looks. This copies one of them
# into REDIS_DATA_DIR once, so switching to the stack does not read as every
# user having been logged out.
#
# Usage:
#   scripts/migrate-redis.sh [--test]
#   scripts/migrate-redis.sh [--test] --from-container NAME
#   scripts/migrate-redis.sh [--test] --from-volume NAME
#   scripts/migrate-redis.sh [--test] --force      # overwrite existing data
#
# It is safe to run twice: without --force it refuses to overwrite a data
# directory that already holds a dataset, and it never deletes the source.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

TEST_STACK=0
FORCE=0
SOURCE_CONTAINER=""
SOURCE_VOLUME=""

while (( $# )); do
    case "$1" in
        --test) TEST_STACK=1 ;;
        --force) FORCE=1 ;;
        --from-container) SOURCE_CONTAINER="${2:?--from-container needs a name}"; shift ;;
        --from-volume) SOURCE_VOLUME="${2:?--from-volume needs a name}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
    shift
done

(( TEST_STACK )) && use_test_stack

require_cmd docker
require_env_file
cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

PASSWORD="$(env_value REDIS_PASSWORD)"
[[ -n "$PASSWORD" ]] || die "REDIS_PASSWORD is not set in ${ENV_FILE#"$ROOT_DIR"/}."

DATA_DIR="$(redis_data_dir)"
STAGING_CONTAINER="korail_redis_migrate_$$"

cleanup() {
    docker rm -f "$STAGING_CONTAINER" >/dev/null 2>&1 || true
    [[ -n "${SCRATCH:-}" ]] && rm -rf "$SCRATCH"
}
trap cleanup EXIT

# ==================== Pick a source ====================

if [[ -z "$SOURCE_CONTAINER" && -z "$SOURCE_VOLUME" ]]; then
    if (( TEST_STACK )); then
        candidate="$(env_value DEV_REDIS_CONTAINER_NAME)"
        candidate="${candidate:-korail_test_dev_redis}"
    else
        candidate="korail_dev_redis"
    fi

    if container_exists "$candidate"; then
        SOURCE_CONTAINER="$candidate"
    else
        # The stack's own history: compose derived this name from the project
        # when the data still lived in a named volume.
        project="${COMPOSE_PROJECT_NAME:-$(basename "$ROOT_DIR" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '_')}"
        for volume in "${project}_redis_data" "${project//_/}_redis_data"; do
            if docker volume inspect "$volume" >/dev/null 2>&1; then
                SOURCE_VOLUME="$volume"
                break
            fi
        done
    fi
fi

if [[ -z "$SOURCE_CONTAINER" && -z "$SOURCE_VOLUME" ]]; then
    err "옮겨올 Redis를 찾지 못했습니다."
    err "  컨테이너 목록:  docker ps -a --format '{{.Names}}'"
    err "  볼륨 목록:      docker volume ls"
    die "  --from-container NAME 또는 --from-volume NAME 으로 직접 지정하세요."
fi

# ==================== Refuse to clobber ====================

mkdir -p "$DATA_DIR"
if [[ -e "${DATA_DIR}/dump.rdb" || -e "${DATA_DIR}/appendonlydir" ]]; then
    if (( ! FORCE )); then
        err "${DATA_DIR} 에 이미 데이터가 있습니다."
        err "이미 옮겼다면 더 할 일이 없습니다. 덮어쓰려면 --force 를 붙이세요."
        exit 1
    fi
    warn "${DATA_DIR} 의 기존 데이터를 덮어씁니다."
    read -r -p "Type 'yes' to continue: " confirmation
    [[ "$confirmation" == "yes" ]] || die "Aborted."
fi

# ==================== Read the source out ====================

SCRATCH="$(mktemp -d)"

if [[ -n "$SOURCE_CONTAINER" ]]; then
    info "원본: 컨테이너 ${SOURCE_CONTAINER}"

    if container_running "$SOURCE_CONTAINER"; then
        # SAVE rather than BGSAVE: it returns when the file on disk is the
        # dataset in memory, which is exactly the thing that has to be true
        # before copying it.
        info "SAVE 로 현재 상태를 디스크에 씁니다"
        docker exec "$SOURCE_CONTAINER" \
            redis-cli -a "$PASSWORD" --no-auth-warning SAVE >/dev/null \
            || die "SAVE 실패. REDIS_PASSWORD 가 ${SOURCE_CONTAINER} 의 것과 같은지 확인하세요."
        SOURCE_KEYS="$(docker exec "$SOURCE_CONTAINER" \
            redis-cli -a "$PASSWORD" --no-auth-warning DBSIZE 2>/dev/null | tr -d '\r')"
    else
        warn "${SOURCE_CONTAINER} 가 멈춰 있습니다. 마지막으로 저장된 스냅숏을 씁니다."
        SOURCE_KEYS="?"
    fi

    docker cp "${SOURCE_CONTAINER}:/data/dump.rdb" "${SCRATCH}/dump.rdb" \
        || die "${SOURCE_CONTAINER}:/data/dump.rdb 를 읽지 못했습니다."
else
    info "원본: 볼륨 ${SOURCE_VOLUME}"
    SOURCE_KEYS="?"
    # A throwaway container is the only way to read a named volume's contents.
    docker run --rm -v "${SOURCE_VOLUME}:/src:ro" -v "${SCRATCH}:/out" \
        redis:7-alpine sh -c 'cp /src/dump.rdb /out/dump.rdb 2>/dev/null' \
        || die "${SOURCE_VOLUME} 안에 dump.rdb 가 없습니다."
fi

[[ -s "${SCRATCH}/dump.rdb" ]] || die "복사한 스냅숏이 비어 있습니다."
info "스냅숏 크기: $(du -h "${SCRATCH}/dump.rdb" | cut -f1)"

# ==================== Write it into the stack's directory ====================

# The stack's Redis runs with appendonly yes, and an AOF-enabled Redis loads
# the AOF and ignores dump.rdb. Dropping the snapshot in and starting the stack
# would therefore come up empty. So: load the snapshot in a throwaway instance
# with the AOF off, then turn it on, which rewrites the loaded dataset into the
# appendonlydir the stack will read.

info "스택 Redis를 멈춥니다 (실행 중이면)"
compose stop redis >/dev/null 2>&1 || true

# Every write into the data directory goes through a container. Redis chowns
# what it owns to its own uid, so the directory is not reliably writable by
# whoever runs this script - and a migration that needs sudo the second time
# it is run is a migration that gets done by hand instead.
docker run --rm -v "${DATA_DIR}:/data" redis:7-alpine \
    sh -c 'rm -rf /data/appendonlydir /data/dump.rdb'

info "스냅숏을 AOF로 변환합니다"
docker create --name "$STAGING_CONTAINER" \
    -v "${DATA_DIR}:/data" \
    redis:7-alpine \
    redis-server --requirepass "$PASSWORD" --appendonly no --save '' >/dev/null
docker cp "${SCRATCH}/dump.rdb" "${STAGING_CONTAINER}:/data/dump.rdb"
docker start "$STAGING_CONTAINER" >/dev/null

staged_keys=""
for _ in $(seq 1 30); do
    if staged_keys="$(docker exec "$STAGING_CONTAINER" \
        redis-cli -a "$PASSWORD" --no-auth-warning DBSIZE 2>/dev/null | tr -d '\r')" \
        && [[ -n "$staged_keys" ]]; then
        break
    fi
    sleep 0.5
done
[[ -n "$staged_keys" ]] || die "임시 Redis가 스냅숏을 읽지 못했습니다. 'docker logs ${STAGING_CONTAINER}' 를 보세요."

docker exec "$STAGING_CONTAINER" \
    redis-cli -a "$PASSWORD" --no-auth-warning CONFIG SET appendonly yes >/dev/null

for _ in $(seq 1 60); do
    if docker exec "$STAGING_CONTAINER" \
        redis-cli -a "$PASSWORD" --no-auth-warning INFO persistence 2>/dev/null \
        | tr -d '\r' | grep -q '^aof_rewrite_in_progress:0'; then
        break
    fi
    sleep 0.5
done

docker exec "$STAGING_CONTAINER" \
    redis-cli -a "$PASSWORD" --no-auth-warning SHUTDOWN NOSAVE >/dev/null 2>&1 || true
docker rm -f "$STAGING_CONTAINER" >/dev/null 2>&1 || true

[[ -d "${DATA_DIR}/appendonlydir" ]] || die "AOF가 만들어지지 않았습니다. ${DATA_DIR} 를 확인하세요."

ok "${staged_keys} 개 키를 ${DATA_DIR} 로 옮겼습니다 (원본 ${SOURCE_KEYS} 개)"

# ==================== Check it from the stack ====================

info "스택 Redis로 확인합니다"
ensure_redis_data_dir
compose up -d --wait redis >/dev/null

final_keys="$(docker exec "$(compose_container redis)" \
    redis-cli -a "$PASSWORD" --no-auth-warning DBSIZE 2>/dev/null | tr -d '\r')"

if [[ "$final_keys" == "$staged_keys" ]]; then
    ok "$(compose_container redis) 에서 ${final_keys} 개 키가 보입니다"
else
    die "옮긴 키는 ${staged_keys} 개인데 스택에서는 ${final_keys} 개가 보입니다."
fi

echo
info "원본은 그대로 두었습니다. 봇이 정상 동작하는 것을 확인한 뒤 정리하세요:"
if [[ -n "$SOURCE_CONTAINER" ]]; then
    info "  docker rm -f ${SOURCE_CONTAINER}"
else
    info "  docker volume rm ${SOURCE_VOLUME}"
fi
