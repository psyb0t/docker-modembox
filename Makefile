IMAGE ?= psyb0t/modembox:local
DEV_IMAGE ?= modembox-dev:local
BROWSER_IMAGE ?= psyb0t/stealthy-auto-browse:latest
DOCS_IMAGE := node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
OPENAPI_IMAGE := openapitools/openapi-generator-cli:v7.27.0@sha256:949efd0d5cf3c093ef169887b2ebe0879ee2e8074b6476264232ce4fd8ef46d0
OPENAPI_DOCUMENT := docs/reference/openapi.json
DOCS_RUN := docker run --rm --cap-drop ALL --security-opt no-new-privileges \
	--user "$$(id -u):$$(id -g)" -e HOME=/tmp -e COREPACK_HOME=/work/website/.corepack \
	-v "$(CURDIR)/docs:/work/docs:ro" \
	-v "$(CURDIR)/website:/work/website" -w /work/website
MODEMBOX_TEST_DEVICE ?= /dev/ttyUSB2

.PHONY: build dev-image lint test test-unit test-integration test-ui test-real test-real-sms-receive sec generate-docs check-openapi docs-build docs-lock docs-audit help
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

generate-docs: ## Export the REST OpenAPI JSON
check-openapi: ## Validate the exported REST OpenAPI JSON and check for drift
generate-docs check-openapi: dev-image
	@mkdir -p .testing
	@set -eu; \
		spec="$$(mktemp "$(CURDIR)/.testing/openapi.XXXXXX.json")"; \
		trap 'rm -f -- "$$spec"' EXIT; \
		docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
			$(DEV_IMAGE) python -m modembox.api_docs > "$$spec"; \
		docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
			--user "$$(id -u):$$(id -g)" -v "$$spec:/openapi.json:ro" \
			$(OPENAPI_IMAGE) validate -i /openapi.json; \
		if [ "$@" = generate-docs ]; then \
			mkdir -p "$(dir $(OPENAPI_DOCUMENT))"; \
			chmod 0644 "$$spec"; \
			mv "$$spec" "$(OPENAPI_DOCUMENT)"; \
		else \
			diff -u "$(OPENAPI_DOCUMENT)" "$$spec"; \
		fi

docs-lock: ## Refresh the Docusaurus lockfile
	$(DOCS_RUN) $(DOCS_IMAGE) corepack pnpm install --lockfile-only --ignore-scripts

docs-audit: ## Scan the locked documentation dependencies
	$(DOCS_RUN) $(DOCS_IMAGE) corepack pnpm audit

docs-build: check-openapi ## Build the Docusaurus site and generated REST reference
	mkdir -p website/.assets/assets website/.assets/reference website/.content/assets
	cp docs/assets/console.png website/.assets/assets/
	cp docs/assets/console.png website/.content/assets/
	cp docs/*.md website/.content/
	cp "$(OPENAPI_DOCUMENT)" website/.assets/reference/openapi.json
	$(DOCS_RUN) $(DOCS_IMAGE) corepack pnpm install --frozen-lockfile --ignore-scripts --store-dir .pnpm-store
	$(DOCS_RUN) --network none $(DOCS_IMAGE) corepack pnpm run build

help: ## List targets
	@awk 'BEGIN { FS = ":.*## " } /^[a-z-]+:.*## / { printf "%-16s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
