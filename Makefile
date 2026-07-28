IMAGE_NAME := geunsam2/korailbot:v3

# Thin wrappers around scripts/ - see scripts/README.md for the full options.

help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

.PHONY: setup
setup:			## Create .env, generate secrets, install dependencies
	./scripts/setup.sh

.PHONY: install
install:		## Install dependencies with pipenv
	pipenv install --dev

.PHONY: run
run:			## Run the application locally
	./scripts/run.sh

.PHONY: shell
shell:			## Open pipenv shell
	pipenv shell

.PHONY: secrets
secrets:		## Generate any missing secrets in .env
	./scripts/gen-secrets.sh

.PHONY: webhook
webhook:		## Show the current Telegram webhook status
	./scripts/set-webhook.sh --info

.PHONY: security-check
security-check:	## Check the local configuration for security mistakes
	./scripts/security-check.sh

.PHONY: requirements
requirements:	## Generate requirements.txt from Pipfile.lock (for Docker)
	pipenv requirements > requirements.txt
	echo "uwsgi==2.0.31" >> requirements.txt

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
