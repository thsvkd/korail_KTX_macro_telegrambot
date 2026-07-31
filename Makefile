# The image the compose stack runs. Defaults to a local tag with no registry
# in it: anyone self-hosting this builds their own rather than pulling an
# image someone else controls. Override to publish under your own namespace:
#   IMAGE_NAME=you/korailbot:latest make docker-build docker-push
IMAGE_NAME ?= korailbot:local

# Thin wrappers around scripts/ - see scripts/README.md for the full options.

help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

.PHONY: setup
setup:			## Create .env, generate secrets, install dependencies
	./scripts/setup.sh

.PHONY: setup-dev
setup-dev:		## Same, plus a developer chat: magic string + fixed Korail account
	./scripts/setup.sh --dev

.PHONY: install
install:		## Install dependencies into .venv from uv.lock
	uv sync --frozen

.PHONY: lock
lock:			## Re-resolve uv.lock after editing pyproject.toml
	uv lock

.PHONY: lint
lint:			## Check formatting and lint rules
	uv run --frozen ruff format --check .
	uv run --frozen ruff check .

.PHONY: format
format:			## Reformat and autofix
	uv run --frozen ruff format .
	uv run --frozen ruff check --fix .

.PHONY: typecheck
typecheck:		## Run mypy
	uv run --frozen mypy

.PHONY: run
run:			## Run the application locally (foreground, replaces a running one)
	./scripts/run.sh

.PHONY: daemon
daemon:			## Run the application in the background, logging to .run/
	./scripts/run.sh --daemon

.PHONY: stop
stop:			## Stop the running application
	./scripts/run.sh --stop

.PHONY: status
status:			## Show what the application is doing
	./scripts/status.sh

.PHONY: dev-redis
dev-redis:		## Start the local development Redis (127.0.0.1:6379)
	./scripts/dev-redis.sh

.PHONY: shell
shell:			## Open a shell with .venv activated
	uv run --frozen $$SHELL

.PHONY: secrets
secrets:		## Generate any missing secrets in .env
	./scripts/gen-secrets.sh

.PHONY: webhook
webhook:		## Show the current Telegram webhook status
	./scripts/set-webhook.sh --info

.PHONY: security-check
security-check:	## Check the local configuration for security mistakes
	./scripts/security-check.sh

.PHONY: test
test:			## Run all tests with pytest
	./scripts/test.sh

.PHONY: test-unit
test-unit:		## Run the unit tests only
	./scripts/test.sh tests/unit -v

.PHONY: build
build:			## Build Docker Image
	./scripts/docker-build.sh ${IMAGE_NAME}

.PHONY: up
up:				## Start the stack with docker compose
	./scripts/docker-up.sh

.PHONY: down
down:			## Stop the stack
	./scripts/docker-down.sh

.PHONY: logs
logs:			## Follow container logs
	./scripts/docker-logs.sh

.PHONY: publish
publish:		## Build and publish the Docker image
	./scripts/docker-push.sh ${IMAGE_NAME}
