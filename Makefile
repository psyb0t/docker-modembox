IMAGE ?= psyb0t/modembox:local
DEV_IMAGE ?= modembox-dev:local
BROWSER_IMAGE ?= psyb0t/stealthy-auto-browse:latest
DOCS_IMAGE := ghcr.io/actions/jekyll-build-pages:v1.0.13@sha256:6791ebfd912185ed59bfb5fb102664fa872496b79f87ff8b9cfba292a7345041
MODEMBOX_TEST_DEVICE ?= /dev/ttyUSB2

.PHONY: build dev-image lint test test-unit test-integration test-ui test-real test-real-sms-receive sec docs-build help
build: ## Build the runtime image and static frontend
	docker build --target runtime -t $(IMAGE) .

dev-image: ## Build the development image
	docker build --target dev -t $(DEV_IMAGE) .

lint: dev-image ## Check Python formatting and lint plus Svelte types
	docker run --rm --network none $(DEV_IMAGE) ruff check src tests
	docker run --rm --network none $(DEV_IMAGE) ruff format --check src tests

test: test-unit test-integration test-ui ## Run the Docker-backed test suites

test-unit: dev-image ## Run codec and USB discovery tests in Docker
	docker run --rm --network none $(DEV_IMAGE) pytest -q -m unit

test-integration: dev-image ## Run API, SQLite, serial, MCP and WebSocket tests in Docker
	docker run --rm --network none $(DEV_IMAGE) pytest -q -m 'not unit'

test-ui: dev-image ## Start a disposable browser and exercise the console in Docker
	bash tests/run-ui.sh "$(DEV_IMAGE)" "$(BROWSER_IMAGE)"

test-real: dev-image ## Read modem identity and capabilities from the selected physical TTY
	docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
		--device "$(MODEMBOX_TEST_DEVICE):/dev/test-modem:rw" \
		$(DEV_IMAGE) python tests/hardware_probe.py

test-real-sms-receive: dev-image ## Send an SMS invitation and wait up to five minutes for the reply
	docker run --rm --init --network none --cap-drop ALL --security-opt no-new-privileges \
		--env-file .env.test --device "$(MODEMBOX_TEST_DEVICE):/dev/test-modem:rw" \
		$(DEV_IMAGE) python tests/hardware_sms_receive.py

sec: dev-image ## Write Python security diagnostics in SARIF
	docker run --rm --network none $(DEV_IMAGE) ruff check --select S --output-format sarif --exit-zero src > sec.sarif

docs-build: ## Build the GitHub Pages documentation locally
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp \
		-e GITHUB_WORKSPACE=/work -e GITHUB_REPOSITORY=psyb0t/docker-modembox \
		-e INPUT_SOURCE=docs -e INPUT_DESTINATION=.testing/docs-site -e INPUT_VERBOSE=false \
		-v "$(CURDIR):/work" $(DOCS_IMAGE)

help: ## List targets
	@awk 'BEGIN { FS = ":.*## " } /^[a-z-]+:.*## / { printf "%-16s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
