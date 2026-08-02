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
start:			## Run in the foreground, replacing a running one  [TEST=1]
	./scripts/server.sh start $(T)

.PHONY: daemon
daemon:			## Run in the background, logging to .run/  [TEST=1]
	./scripts/server.sh start --daemon $(T)

.PHONY: stop
stop:			## Stop the running bot  [TEST=1]
	./scripts/server.sh stop $(T)

.PHONY: restart
restart:		## Stop and start again in the background  [TEST=1]
	./scripts/server.sh restart $(T)

.PHONY: status
status:			## Show what the bot is doing  [TEST=1]
	./scripts/server.sh status $(T)

.PHONY: logs
logs:			## Follow the daemon's log  [TEST=1]
	./scripts/server.sh logs -f $(T)

.PHONY: redis
redis:			## Start the local development Redis  [TEST=1]
	./scripts/server.sh redis start $(T)

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
down:			## Stop the stack  [TEST=1]
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
