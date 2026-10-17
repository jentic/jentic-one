VERSION  := $(shell ./scripts/version.sh)
GIT_SHA  := $(shell git rev-parse --short HEAD)
IMG_PREFIX := jentic-one
SERVICES := app registry admin control broker

BUILD_DIR := build

.PHONY: help install sync lock upgrade fmt format fix lint typecheck test test-unit test-fast test-integration test-integration-sqlite test-integration-all test-arch test-smoke cov cov-all check score openapi openapi-parity config-schema config-reference endpoints cli-reference broker-reference skills hooks clean dev start-fixtures stop-fixtures destroy-fixtures start-app start-registry start-admin start-control start-broker start-local build-wheel build-base build-all save-all images check-image-lock release-image $(addprefix build-,$(SERVICES)) $(addprefix push-,$(SERVICES)) $(addprefix save-,$(SERVICES)) perf-build perf-up perf-provision perf-overhead perf-dbcost perf-capacity perf-profile perf-ui perf-load perf-obs perf-report perf-down

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

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

config-schema: ## Regenerate the backend config JSON Schema (config/config-schema.json) from the AppConfig model
	uv run python -m tools.config_schema_export
	@echo "Regenerated config/config-schema.json."
	@echo "Run 'cd cli && make generate-config' to refresh the generated installer config struct."
	@echo "Run 'make config-reference' to refresh the configuration reference doc."

config-reference: ## Regenerate the configuration reference (docs/reference/config.md) from the AppConfig model
	uv run python -m tools.config_reference
	@echo "Regenerated docs/reference/config.md."
endpoints: ## Regenerate the endpoint + permission reference (docs/reference/endpoints.{md,json}) from code
	uv run python -m tools.endpoint_tree
	@echo "Regenerated docs/reference/endpoints.md and docs/reference/endpoints.json."

cli-reference: ## Regenerate the CLI command reference (ui/public/cli-reference.json) from the cobra command tree
	cd cli && go run ./cmd/clidocs -o ../ui/public/cli-reference.json
	@echo "Regenerated ui/public/cli-reference.json."

broker-reference: ## Regenerate the Broker OpenAPI artifact (ui/public/broker-openapi.json) from the hand-curated spec
	uv run python -m tools.broker_reference
	@echo "Regenerated ui/public/broker-openapi.json."

skills: ## Mirror skills/<name>/SKILL.md into both content/ dirs (CLI embed + wheel-served copies)
	uv run python -m tools.skills_sync
	@echo "Mirrored the served skill set; run 'go build ./...' in cli/ to re-embed."

SCORECARD_CLI := @jentic/api-scorecard-cli@1.12.0

score: ## Score the control + broker OpenAPI specs with the Jentic API Scorecard (needs JENTIC_SCORE_API_KEY; SCORECARD_MIN_SCORE gates)
	@# Opt-in: skips cleanly without a key so `make check` stays green offline.
	@# The CLI reads JENTIC_API_KEY; the key is handed to it per-invocation only.
	@# The registry override keeps a user-level `@jentic:` scope mapping (e.g. to
	@# GitHub Packages) from shadowing the public npm package.
	@if [ -z "$$JENTIC_SCORE_API_KEY" ]; then \
		echo "JENTIC_SCORE_API_KEY is not set; skipping OpenAPI scorecard."; \
		exit 0; \
	fi; \
	status=0; \
	for spec in control broker; do \
		file="openapi/$$spec/$$spec.openapi.yaml"; \
		echo "==> scoring $$file"; \
		if [ -z "$$SCORECARD_MIN_SCORE" ]; then \
			JENTIC_API_KEY="$$JENTIC_SCORE_API_KEY" npx --yes --@jentic:registry=https://registry.npmjs.org \
				$(SCORECARD_CLI) score "$$file" --quiet || status=1; \
		else \
			score=$$(JENTIC_API_KEY="$$JENTIC_SCORE_API_KEY" npx --yes --@jentic:registry=https://registry.npmjs.org \
				$(SCORECARD_CLI) score "$$file" --quiet --format json --detail summary | jq -r '.summary.score'); \
			case "$$score" in ''|null) echo "FAIL: could not read a score for $$spec"; status=1; continue;; esac; \
			if awk -v s="$$score" -v m="$$SCORECARD_MIN_SCORE" 'BEGIN { exit !(s < m) }'; then \
				echo "FAIL: $$spec scored $$score (< $$SCORECARD_MIN_SCORE)"; status=1; \
			else \
				echo "ok: $$spec scored $$score (>= $$SCORECARD_MIN_SCORE)"; \
			fi; \
		fi; \
	done; \
	exit $$status

detect-secrets: ## Check for new secrets not in baseline
	uv run detect-secrets scan --baseline .secrets.baseline --exclude-files '\.git/'

check: lint detect-secrets test-arch score ## Run lint, secrets audit, arch-tests, and score (score last: it needs npx/network and shouldn't block the offline gates)

hooks: ## Install lefthook git hooks (pre-commit + commit-msg)
	uv run lefthook install

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov coverage.xml build dist *.egg-info $(BUILD_DIR)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find deploy/helm -name "*.tgz" -delete 2>/dev/null || true
	find deploy/helm -name "Chart.lock" -delete 2>/dev/null || true

JENTIC_CONFIG_FILE ?= config/local.yaml
export JENTIC_CONFIG_FILE

dev: ## One-command local bring-up (idempotent): fixtures + migrations + UI, then start the app
	@./scripts/dev-up.sh
	@$(MAKE) --no-print-directory start-app

start-fixtures: ## Start Docker database fixtures and apply migrations
	@./scripts/setup.sh

stop-fixtures: ## Stop Docker database fixtures
	docker compose -f docker/local-setup/docker-compose.yaml stop

destroy-fixtures: ## Remove Docker database fixtures and volumes
	docker compose -f docker/local-setup/docker-compose.yaml down -v

start-app: ## Start combined app (all surfaces)
	uv run python -m jentic_one

start-registry: ## Start registry surface standalone
	JENTIC__APPS=registry uv run python -m jentic_one

start-admin: ## Start admin surface standalone
	JENTIC__APPS=admin uv run python -m jentic_one

start-control: ## Start control surface standalone
	JENTIC__APPS=control uv run python -m jentic_one

# The standalone broker answers on :8100, but the admin API (jobs, approvals)
# and the web UI live on the combined app at :8000. Point the broker's
# absolute links (async-job and held-call `_links`, the approval `review_url`)
# there; its own request origin would name the broker instead.
# scripts/start-local.sh sets the same broker env for `start-local`.
LOCAL_BROKER_ENV := JENTIC__APPS=broker JENTIC__SERVER__PORT=8100 \
	JENTIC__BROKER__JOBS_API_BASE_URL=http://127.0.0.1:8000

start-broker: ## Start broker surface standalone on :8100 (runs alongside start-app on :8000)
	$(LOCAL_BROKER_ENV) uv run python -m jentic_one

start-local: ## Start the combined app (:8000) and the broker (:8100) together for local testing
	@echo "==> combined app → http://127.0.0.1:8000   broker → http://127.0.0.1:8100 (Ctrl-C stops both)"
	@scripts/start-local.sh

migrate-sqlite: ## Apply all migrations to the local SQLite databases (config/local-sqlite.yaml)
	@mkdir -p .data
	JENTIC_CONFIG_FILE=config/local-sqlite.yaml uv run python -m jentic_one.migrations.run

start-app-sqlite: migrate-sqlite ## Start combined app on local SQLite (ingest only; search disabled)
	JENTIC_CONFIG_FILE=config/local-sqlite.yaml uv run python -m jentic_one

build-wheel: ## Build Python wheel (packaging TEST ONLY — never published; the container image is the canonical backend distribution)
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
# NOTE: push-<svc> pushes the local per-service names ($(IMG_PREFIX)/<svc>) for
# dev/fork registries; the published-release path is `release-image` below,
# which pushes the differently named <REGISTRY>/jentic-one-app.
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

check-image-lock: ## Fail if IMAGE's Python packages differ from uv.lock (IMAGE=<tag>, optional PLATFORM=linux/arm64)
	@test -n "$(IMAGE)" || { echo "usage: make check-image-lock IMAGE=<tag> [PLATFORM=linux/<arch>]"; exit 1; }
	./scripts/check-image-lock.sh "$(IMAGE)" $(if $(PLATFORM),--platform "$(PLATFORM)")

# ─── Release (publish the app image to a real registry) ───────────────────
# One `app` image serves every surface — the surface set is chosen at runtime
# via JENTIC__APPS (see deploy/README.md "One image, runtime surfaces"). So
# publishing the single `app` image is enough for a self-hosted app + broker
# deployment.
#
#   make release-image REGISTRY=ghcr.io/jentic
#
# builds deploy/docker/app.multiarch.Dockerfile for linux/amd64 + linux/arm64
# with `docker buildx` and pushes the OCI index to $(REGISTRY)/jentic-one-app
# tagged with the pyproject version and the short git SHA. `latest` only moves
# when the version is a stable X.Y.Z (no prerelease suffix), mirroring CI's
# guard so a dev/rc push can never hijack the tag self-hosters float on.
# Requires `docker login <registry>` first AND a buildx builder that can build
# both arches (`docker buildx create --use` + QEMU for the non-native leg). CI
# does this on a vX.Y.Z tag (see .github/workflows/release.yml) with its own
# inline script rather than this target: CI must scan each arch, interleave
# cosign signing between the version and :latest tags, and lower-case the GHCR
# owner — which this all-in-one target can't express.
REGISTRY ?=
RELEASE_IMAGE := $(REGISTRY)/jentic-one-app
RELEASE_PLATFORMS ?= linux/amd64,linux/arm64

release-image: ## Build + push the multi-arch app image to REGISTRY (e.g. REGISTRY=ghcr.io/jentic)
	@if [ -z "$(REGISTRY)" ]; then \
		echo "ERROR: set REGISTRY, e.g. make release-image REGISTRY=ghcr.io/jentic"; exit 1; \
	fi
	docker buildx build -f deploy/docker/app.multiarch.Dockerfile \
		--platform $(RELEASE_PLATFORMS) \
		-t $(RELEASE_IMAGE):$(VERSION) \
		-t $(RELEASE_IMAGE):$(GIT_SHA) \
		--push .
	@if echo "$(VERSION)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$'; then \
		docker buildx imagetools create -t $(RELEASE_IMAGE):latest $(RELEASE_IMAGE):$(VERSION); \
		echo "Pushed $(RELEASE_IMAGE) ($(VERSION), $(GIT_SHA), latest) [$(RELEASE_PLATFORMS)]"; \
	else \
		echo "Prerelease version — not moving :latest"; \
		echo "Pushed $(RELEASE_IMAGE) ($(VERSION), $(GIT_SHA)) [$(RELEASE_PLATFORMS)]"; \
	fi

# ─── Broker perf / load suite (tests/perf, see tests/perf/README.md) ──────
# Local-only: a self-contained compose stack (Postgres + app + broker + the
# smoke-upstream harness) with in-network load generators. Never run in CI.
PERF_COMPOSE := docker compose -f tests/perf/docker-compose.perf.yaml
PERF_USERS ?= 50
PERF_SPAWN ?= 10
PERF_TIME ?= 2m
PERF_CLASSES ?= BrokerEchoUser
PERF_WORKERS_LOCUST ?= 2

perf-build: build-base ## Perf: build jentic-one/app:perf from this checkout
	docker build -f deploy/docker/app.Dockerfile -t jentic-one/app:perf .

perf-up: ## Perf: start db + migrate + app + broker + upstream harness
	@mkdir -p tests/perf/results
	$(PERF_COMPOSE) up -d --wait postgres migrate app broker smoke-upstream

perf-provision: ## Perf: vendors, credentials, bindings, callers -> tests/perf/results/.perf-env.json
	uv run python -m tests.perf.provision

perf-overhead: ## Perf: oha open-loop A/B, direct vs via broker (ARGS="--quick" / "--only a,b")
	uv run python -m tests.perf.bench_overhead $(ARGS)

perf-dbcost: ## Perf: per-call statements/transactions per DB plane (pg_stat_statements)
	uv run python -m tests.perf.dbcost --all $(ARGS)

perf-capacity: ## Perf: closed-loop throughput sweep, workers x CPUs x concurrency (ARGS="--configs 1x2,2x4")
	uv run python -m tests.perf.bench_capacity $(ARGS)

perf-profile: ## Perf: py-spy CPU profile of the broker under closed-loop load (ARGS="--concurrency 32")
	uv run python -m tests.perf.pyspy_profile $(ARGS)

perf-ui: ## Perf: Locust web UI (master + PERF_WORKERS_LOCUST workers) on http://127.0.0.1:8089
	PERF_WORKERS_LOCUST=$(PERF_WORKERS_LOCUST) $(PERF_COMPOSE) --profile load up -d \
		--scale locust-worker=$(PERF_WORKERS_LOCUST) locust-master locust-worker
	@echo "Locust UI: http://127.0.0.1:8089"

# A PERF_SHAPE ends the run itself (its tick returns None), so -t is left off:
# a fixed PERF_TIME would cut a step ramp short of PERF_MAX_USERS.
perf-load: ## Perf: headless Locust run (PERF_CLASSES, PERF_USERS, PERF_SPAWN, PERF_TIME, PERF_SHAPE)
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); \
	$(PERF_COMPOSE) --profile load run --rm -T --no-deps locust-master \
		-f /work/tests/perf/locustfile.py --headless -u $(PERF_USERS) -r $(PERF_SPAWN) \
		$(if $(PERF_SHAPE),,-t $(PERF_TIME)) \
		--only-summary --csv /work/tests/perf/results/$$stamp-locust \
		--html /work/tests/perf/results/$$stamp-locust.html $(PERF_CLASSES)

perf-obs: ## Perf: Prometheus (:19090) + Grafana (:13000, "Broker perf" dashboard)
	$(PERF_COMPOSE) --profile obs up -d --wait prometheus grafana postgres-exporter
	@echo "Grafana: http://127.0.0.1:13000/d/jentic-broker-perf"

perf-report: ## Perf: render tests/perf/results/*.json into results/report.html
	uv run python -m tests.perf.report

perf-down: ## Perf: stop the perf stack and wipe its volumes
	$(PERF_COMPOSE) --profile load --profile tools --profile obs down -v --remove-orphans
