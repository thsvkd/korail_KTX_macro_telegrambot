# The image the compose stack runs. Defaults to a local tag with no registry
# in it: anyone self-hosting this builds their own rather than pulling an
# image someone else controls. Override to publish under your own namespace:
#   IMAGE_NAME=you/korailbot:latest make build publish
IMAGE_NAME ?= korailbot:local

# Every target that can act on the staging bot does so with TEST=1, rather
# than each having a '-test' twin:
#   make status          # the production bot
#   make status TEST=1   # the one in .env.test
TEST ?=
T := $(if $(TEST),--test,)

# The bot runs as the docker compose stack. HOST=1 runs it from .venv instead,
# against a separate development Redis, for debugging with a local interpreter:
#   make start           # the compose stack
#   make start HOST=1    # a .venv process, its own Redis, not the deployed data
HOST ?=
H := $(if $(HOST),--host,)

# Thin wrappers around scripts/ - see scripts/README.md for the full options.

help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

##
## ── 준비 ────────────────────────────────────────────────

.PHONY: bootstrap
bootstrap:		## Install or update .venv from uv.lock (idempotent)
	./scripts/bootstrap.sh

.PHONY: doctor
doctor:			## Report what this machine is missing, change nothing
	./scripts/bootstrap.sh --check

.PHONY: setup
setup:			## First-time init: .env, secrets, dependencies  [TEST=1]
	./scripts/setup.sh $(T)

.PHONY: setup-dev
setup-dev:		## Same, plus a developer chat: magic string + fixed railway accounts
	./scripts/setup.sh --dev

.PHONY: onboarding
onboarding:		## Guided first-time setup, from bot token to first reply
	./scripts/setup.sh onboarding

.PHONY: secrets
secrets:		## Generate any missing secrets  [TEST=1]
	./scripts/setup.sh secrets $(T)

.PHONY: check
check:			## Check the configuration for security mistakes  [TEST=1]
	./scripts/setup.sh check $(T)

.PHONY: lock
lock:			## Re-resolve uv.lock after editing pyproject.toml
	uv lock

##
## ── 서버 ────────────────────────────────────────────────

.PHONY: start
start:			## Start the stack (app + Redis) in the background  [TEST=1] [HOST=1]
	./scripts/server.sh start $(T) $(H)

.PHONY: foreground
foreground:		## Same, attached to this terminal  [TEST=1] [HOST=1]
	./scripts/server.sh start --foreground $(T) $(H)

.PHONY: daemon
daemon: start		## Alias for start, kept for muscle memory  [TEST=1] [HOST=1]

.PHONY: stop
stop:			## Stop the bot, keeping every bit of Redis data  [TEST=1] [HOST=1]
	./scripts/server.sh stop $(T) $(H)

.PHONY: restart
restart:		## Recreate the app container with the current config  [TEST=1] [HOST=1]
	./scripts/server.sh restart $(T) $(H)

.PHONY: rebuild
rebuild:		## Rebuild the image and restart the app  [TEST=1]
	./scripts/server.sh restart --build $(T)

.PHONY: status
status:			## Show what the bot is doing  [TEST=1] [HOST=1]
	./scripts/server.sh status $(T) $(H)

.PHONY: logs
logs:			## Follow the bot's log  [TEST=1] [HOST=1]
	./scripts/server.sh logs -f $(T) $(H)

.PHONY: redis
redis:			## Start Redis on its own  [TEST=1] [HOST=1]
	./scripts/server.sh redis start $(T) $(H)

.PHONY: migrate-redis
migrate-redis:		## Copy a standalone Redis container's data into the stack  [TEST=1]
	./scripts/migrate-redis.sh $(T)

.PHONY: redis-cli
redis-cli:		## Open redis-cli against whichever Redis the bot uses  [TEST=1]
	./scripts/server.sh redis-cli $(T)

##
## ── 검사 ────────────────────────────────────────────────

.PHONY: test
test:			## Run all tests with pytest
	./scripts/test.sh

.PHONY: test-unit
test-unit:		## Run the unit tests only (no Docker needed)
	./scripts/test.sh tests/unit -v

.PHONY: lint
lint:			## Check formatting and lint rules
	./scripts/lint.sh

.PHONY: format
format:			## Reformat and autofix what ruff can
	./scripts/lint.sh --fix

.PHONY: lint-all
lint-all:		## Also run mypy and shellcheck (report only)
	./scripts/lint.sh --all

.PHONY: typecheck
typecheck:		## Run mypy
	uv run --frozen mypy

##
## ── 배포 ────────────────────────────────────────────────

.PHONY: build
build:			## Build the Docker image  [TEST=1]
	./scripts/deploy.sh $(T) build $(if $(TEST),,$(IMAGE_NAME))

.PHONY: up
up:				## Start the stack with docker compose  [TEST=1]
	./scripts/deploy.sh $(T) up

.PHONY: down
down:			## Stop and remove the containers, keeping the Redis data  [TEST=1]
	./scripts/deploy.sh $(T) down

.PHONY: docker-logs
docker-logs:		## Follow the compose stack's logs  [TEST=1]
	./scripts/deploy.sh $(T) logs

.PHONY: publish
publish:		## Build and publish the Docker image
	./scripts/deploy.sh push $(IMAGE_NAME)

##
## ── 그 밖에 ──────────────────────────────────────────────

.PHONY: shell
shell:			## Open a shell with .venv activated
	uv run --frozen $$SHELL

##
## TEST=1 을 붙이면 .env.test 의 스테이징 봇을 대상으로 합니다.
## HOST=1 을 붙이면 컨테이너 대신 .venv 프로세스를 대상으로 합니다(디버깅용).
##
## 봇과 Redis는 docker compose로 뜹니다. Redis 데이터는 이름 있는 볼륨이 아니라
## REDIS_DATA_DIR(기본 ./.data/redis)에 그대로 저장되므로, 컨테이너를 멈추거나
## 지워도 - down --volumes 를 해도 - 등록 계정과 검색 상태는 남습니다.
