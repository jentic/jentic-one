VERSION  := $(shell ./scripts/version.sh)
GIT_SHA  := $(shell git rev-parse --short HEAD)
IMG_PREFIX := jentic-one
SERVICES := app registry admin control broker

BUILD_DIR := build

.PHONY: help install sync lock upgrade fmt format fix lint typecheck test test-unit test-fast test-integration test-integration-sqlite test-integration-all test-arch test-smoke cov cov-all check score openapi openapi-parity endpoints cli-reference broker-reference hooks clean start-fixtures stop-fixtures destroy-fixtures start-app start-registry start-admin start-control start-broker build-wheel build-base build-all save-all images release-image $(addprefix build-,$(SERVICES)) $(addprefix push-,$(SERVICES)) $(addprefix save-,$(SERVICES))

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: sync ui-setup hooks ## Full dev setup: sync deps, install UI deps, install lefthook hooks

sync: ## Install/sync project + dev dependencies (includes all extras)
	uv sync --dev --all-extras

lock: ## Refresh the lockfile
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade
	uv sync --dev

fmt format: ## Format code with ruff
	uv run ruff format .
	uv run ruff check . --fix

fix: ## Auto-fix lint issues and reformat code
	uv run ruff check --fix .
	uv run ruff format .

lint: ## Lint (ruff check + format check + mypy)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

typecheck: ## Run mypy
	uv run mypy

test: test-unit ## Run tests (unit only by default)

test-unit: ## Run unit tests
	uv run pytest tests/unit/

test-fast: ## Run unit + arch tests (no external services)
	uv run pytest tests/unit/ tests/arch/

test-integration: ## Run all integration tests against PostgreSQL (requires running fixtures)
	uv run pytest tests/ -m integration --no-cov

test-integration-sqlite: ## Run backend-agnostic integration tests against SQLite (no external services)
	JENTIC_TEST_BACKEND=sqlite uv run pytest tests/integration/ -m integration --no-cov

test-integration-all: test-integration test-integration-sqlite ## Run integration tests on both backends

test-arch: ## Run architecture enforcement tests
	uv run pytest tests/arch/ -m arch --no-cov

test-smoke: ## Run smoke tests (requires running services)
	uv run pytest tests/smoke/ -m smoke --no-cov

smoke-packaging: ## Build UI+wheel, install in a clean venv, verify the SPA is packaged & served (DB-free)
	./scripts/spa_packaging_smoke.sh

cov: ## Run unit + arch tests with coverage report
	uv run pytest tests/unit/ tests/arch/ --cov-report=term-missing --cov-report=html

cov-all: ## Run all tests with coverage (requires running fixtures/services)
	uv run pytest tests/ --cov-report=term-missing --cov-report=html

openapi: ## Regenerate the control-plane OpenAPI spec (+ UI client schema) from code
	uv run python -m tools.openapi_export
	uv run python -m tools.openapi_export --output ui/openapi.json
	@echo "Regenerated openapi/control/control.openapi.yaml and ui/openapi.json."
	@echo "If working on the UI, run 'cd ui && npm run codegen' to refresh the client."

openapi-parity: ## Print the reference-vs-generated OpenAPI coverage report
	uv run python -m tools.openapi_parity

endpoints: ## Regenerate the endpoint + scope reference (docs/reference/endpoints.{md,json}) from code
	uv run python -m tools.endpoint_tree
	@echo "Regenerated docs/reference/endpoints.md and docs/reference/endpoints.json."

cli-reference: ## Regenerate the CLI command reference (ui/public/cli-reference.json) from the cobra command tree
	cd cli && go run ./cmd/clidocs -o ../ui/public/cli-reference.json
	@echo "Regenerated ui/public/cli-reference.json."

broker-reference: ## Regenerate the Broker OpenAPI artifact (ui/public/broker-openapi.json) from the hand-curated spec
	uv run python -m tools.broker_reference
	@echo "Regenerated ui/public/broker-openapi.json."

score: ## Validate OpenAPI specs with the Jentic API Scorecard CLI (requires 80+)
	# control.openapi.yaml is generated from code (make openapi) and carries the
	# full metadata catalogue, so it must clear the 80+ scorecard floor.
	npx --yes @jentic/api-scorecard-cli@1.0.0-alpha.29 score openapi/control/control.openapi.yaml --quiet
	# broker.openapi.yaml is still hand-curated; re-enable once it is brought up to floor.
	# npx --yes @jentic/api-scorecard-cli@1.0.0-alpha.29 score openapi/broker/broker.openapi.yaml --quiet

detect-secrets: ## Check for new secrets not in baseline
	uv run detect-secrets scan --baseline .secrets.baseline --exclude-files '\.git/'

check: lint score detect-secrets test-arch ## Run lint, score, secrets audit, and arch-tests

hooks: ## Install lefthook git hooks (pre-commit + commit-msg)
	uv run lefthook install

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov coverage.xml build dist *.egg-info $(BUILD_DIR)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find deploy/helm -name "*.tgz" -delete 2>/dev/null || true
	find deploy/helm -name "Chart.lock" -delete 2>/dev/null || true

start-fixtures: ## Start Docker database fixtures and apply migrations
	@./scripts/setup.sh

stop-fixtures: ## Stop Docker database fixtures
	docker compose -f docker/local-setup/docker-compose.yaml stop

destroy-fixtures: ## Remove Docker database fixtures and volumes
	docker compose -f docker/local-setup/docker-compose.yaml down -v

JENTIC_CONFIG_FILE ?= config/local.yaml
export JENTIC_CONFIG_FILE

start-app: ## Start combined app (all surfaces)
	uv run python -m jentic_one

start-registry: ## Start registry surface standalone
	JENTIC__APPS=registry uv run python -m jentic_one

start-admin: ## Start admin surface standalone
	JENTIC__APPS=admin uv run python -m jentic_one

start-control: ## Start control surface standalone
	JENTIC__APPS=control uv run python -m jentic_one

start-broker: ## Start broker surface standalone
	JENTIC__APPS=broker uv run python -m jentic_one

migrate-sqlite: ## Apply all migrations to the local SQLite databases (config/local-sqlite.yaml)
	@mkdir -p .data
	JENTIC_CONFIG_FILE=config/local-sqlite.yaml uv run python -m jentic_one.migrations.run

start-app-sqlite: migrate-sqlite ## Start combined app on local SQLite (ingest only; search disabled)
	JENTIC_CONFIG_FILE=config/local-sqlite.yaml uv run python -m jentic_one

build-wheel: ## Build Python wheel
	uv build --wheel

# ─── UI (frontend) ───────────────────────────────────────────────────────
UI_DIR := ui

ui-setup: ## Install UI deps for local dev (no-op when node is unavailable)
	@if command -v node >/dev/null 2>&1; then \
		echo "Installing UI dependencies…"; \
		cd $(UI_DIR) && npm ci; \
	else \
		echo "node not found — skipping UI deps (backend-only setup)."; \
		echo "Install Node.js if you intend to work on the UI."; \
	fi

ui-install: ## Install UI dependencies
	cd $(UI_DIR) && npm ci

ui-build: ## Build the UI bundle into ui/dist
	cd $(UI_DIR) && npm ci && npm run build

ui-lint: ## Lint the UI
	cd $(UI_DIR) && npm ci && npm run lint

ui-test: ## Run UI unit/component tests
	cd $(UI_DIR) && npm ci && npm run test:run

build-base: ## Build the Python base Docker image (builder + runtime stages)
	docker build -f deploy/docker/python-base.Dockerfile --target builder -t python-base:builder .
	docker build -f deploy/docker/python-base.Dockerfile --target runtime -t python-base:runtime .

# Per-service build / push / save rules generated explicitly (no pattern rules,
# so this works on GNU Make 3.81 — Apple's bundled version).
define SERVICE_RULES
build-$(1): build-base ## Build Docker image for $(1)
	docker build -f deploy/docker/$(1).Dockerfile -t $(IMG_PREFIX)/$(1):$(VERSION) -t $(IMG_PREFIX)/$(1):$(GIT_SHA) .

push-$(1): ## Push $(1) image to the registry
	docker push $(IMG_PREFIX)/$(1):$(VERSION)
	docker push $(IMG_PREFIX)/$(1):$(GIT_SHA)

save-$(1): ## Save $(1) image to build/jentic-$(1)-$(VERSION).tar
	@mkdir -p $(BUILD_DIR)
	docker save $(IMG_PREFIX)/$(1):$(VERSION) -o $(BUILD_DIR)/jentic-$(1)-$(VERSION).tar
	@echo "Wrote $(BUILD_DIR)/jentic-$(1)-$(VERSION).tar"
endef

$(foreach svc,$(SERVICES),$(eval $(call SERVICE_RULES,$(svc))))

build-all: build-base $(addprefix build-,$(SERVICES)) ## Build all Docker images

save-all: $(addprefix save-,$(SERVICES)) ## Save all built images as tarballs under build/

images: ## List locally built jentic-one images
	@docker images "$(IMG_PREFIX)/*"

# ─── Release (publish the app image to a real registry) ───────────────────
# One `app` image serves every surface — the surface set is chosen at runtime
# via JENTIC__APPS (see deploy/README.md "Self-hosted"). So publishing the
# single `app` image is enough for a self-hosted app + broker deployment.
#
#   make release-image REGISTRY=ghcr.io/jentic
#
# builds deploy/docker/app.Dockerfile and pushes it to
# $(REGISTRY)/jentic-one-app tagged with the pyproject version, the short git
# SHA, and `latest`. Requires `docker login <registry>` first. CI does this
# automatically on a vX.Y.Z tag (see .github/workflows/release.yml).
REGISTRY ?=
RELEASE_IMAGE := $(REGISTRY)/jentic-one-app

release-image: build-base ## Build + push the app image to REGISTRY (e.g. REGISTRY=ghcr.io/jentic)
	@if [ -z "$(REGISTRY)" ]; then \
		echo "ERROR: set REGISTRY, e.g. make release-image REGISTRY=ghcr.io/jentic"; exit 1; \
	fi
	docker build -f deploy/docker/app.Dockerfile \
		-t $(RELEASE_IMAGE):$(VERSION) \
		-t $(RELEASE_IMAGE):$(GIT_SHA) \
		-t $(RELEASE_IMAGE):latest .
	docker push $(RELEASE_IMAGE):$(VERSION)
	docker push $(RELEASE_IMAGE):$(GIT_SHA)
	docker push $(RELEASE_IMAGE):latest
	@echo "Pushed $(RELEASE_IMAGE) ($(VERSION), $(GIT_SHA), latest)"
