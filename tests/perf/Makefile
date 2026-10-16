# Broker perf / load suite — local only, never in CI. See README.md.
#
# Run from the repo root as `make perf-<target>` (the root Makefile passes
# through) or here as `make <target>`. `make perf-help` lists everything.

ROOT := $(abspath ../..)
COMPOSE := docker compose -f $(ROOT)/tests/perf/docker-compose.perf.yaml
PY := cd $(ROOT) && uv run python -m
AGENT := $(PY) tests.perf.agent

PERF_USERS ?= 50
PERF_SPAWN ?= 10
PERF_TIME ?= 2m
PERF_CLASSES ?= BrokerEchoUser
PERF_WORKERS_LOCUST ?= 2

# Agent probe knobs (all optional): N, CALLER, CACHE, VENDOR, C, DURATION,
# FUNCS, SECONDS, SCENARIOS, BASE, HEAD, TOLERANCE.
agent_opt = $(if $($(1)),$(2) $($(1)))

.PHONY: help build up provision overhead dbcost capacity profile ui load obs report down \
	agent-doctor agent-sql agent-stages agent-cpu agent-resilience agent-compare

help: ## Show perf targets (humans, then agents)
	@printf "Perf suite (tests/perf). From the repo root use: make perf-<target>\n\n"
	@printf "Stack / humans:\n"
	@awk 'BEGIN {FS = ":.*##"} /^[a-z][a-z-]*:.*?## / && $$1 !~ /^agent-/ { printf "  \033[36mperf-%-17s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\nAgent probes (markdown on stdout, last line ARTIFACT: <json>; exit 0 ok, 2 prereq, 3 regression):\n"
	@awk 'BEGIN {FS = ":.*##"} /^agent-[a-z-]*:.*?## / { printf "  \033[36mperf-%-17s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\nAgent loop: perf-agent-doctor -> a probe -> read tables + cited file:line -> fix ->\n"
	@printf "  perf-agent-compare BASE=<artifact> (exit 3 = regressed). File issues with gh using\n"
	@printf "  .github/ISSUE_TEMPLATE/*.yml; dedupe with gh issue list --search ... --state all.\n"

# ─── stack ───────────────────────────────────────────────────────────────────

build: ## Build jentic-one/app:perf from this checkout (rebuild after src changes)
	$(MAKE) -C $(ROOT) build-base
	cd $(ROOT) && docker build -f deploy/docker/app.Dockerfile -t jentic-one/app:perf .

up: ## Start db + migrate + app + broker + upstream harness
	@mkdir -p $(ROOT)/tests/perf/results
	$(COMPOSE) up -d --wait postgres migrate app broker smoke-upstream

provision: ## Vendors, credentials, bindings, callers -> results/.perf-env.json
	$(PY) tests.perf.provision

down: ## Stop the perf stack and wipe its volumes
	$(COMPOSE) --profile load --profile tools --profile obs down -v --remove-orphans

# ─── benchmarks (humans; JSON under results/) ───────────────────────────────

overhead: ## oha open-loop A/B, direct vs via broker (ARGS="--quick" / "--only a,b")
	$(PY) tests.perf.bench_overhead $(ARGS)

dbcost: ## Per-call statements/transactions per DB plane (pg_stat_statements)
	$(PY) tests.perf.dbcost --all $(ARGS)

capacity: ## Closed-loop throughput sweep, workers x CPUs (ARGS="--configs 1x2,2x4")
	$(PY) tests.perf.bench_capacity $(ARGS)

profile: ## py-spy flamegraph + CPU share per subsystem (ARGS="--concurrency 32")
	$(PY) tests.perf.pyspy_profile $(ARGS)

ui: ## Locust web UI (master + PERF_WORKERS_LOCUST workers) on http://127.0.0.1:8089
	PERF_WORKERS_LOCUST=$(PERF_WORKERS_LOCUST) $(COMPOSE) --profile load up -d \
		--scale locust-worker=$(PERF_WORKERS_LOCUST) locust-master locust-worker
	@echo "Locust UI: http://127.0.0.1:8089"

# A PERF_SHAPE ends the run itself (its tick returns None), so -t is left off:
# a fixed PERF_TIME would cut a step ramp short of PERF_MAX_USERS.
load: ## Headless Locust run (PERF_CLASSES, PERF_USERS, PERF_SPAWN, PERF_TIME, PERF_SHAPE)
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); \
	$(COMPOSE) --profile load run --rm -T --no-deps locust-master \
		-f /work/tests/perf/locustfile.py --headless -u $(PERF_USERS) -r $(PERF_SPAWN) \
		$(if $(PERF_SHAPE),,-t $(PERF_TIME)) \
		--only-summary --csv /work/tests/perf/results/$$stamp-locust \
		--html /work/tests/perf/results/$$stamp-locust.html $(PERF_CLASSES)

obs: ## Prometheus (:19090) + Grafana (:13000, "Broker perf" dashboard)
	$(COMPOSE) --profile obs up -d --wait prometheus grafana postgres-exporter
	@echo "Grafana: http://127.0.0.1:13000/d/jentic-broker-perf"

report: ## Render results/*.json into results/report.html
	$(PY) tests.perf.report

# ─── agent probes ────────────────────────────────────────────────────────────

agent-doctor: ## Pre-flight: stack healthy, tokens valid, image built from this src (prints fixes)
	@$(AGENT) doctor

agent-sql: ## SQL per call: sessions/pings/tx per plane, statements by (SQL, file:line), timeline, EXPLAIN [N CALLER CACHE=warm|cold VENDOR] ~1 min
	@$(AGENT) sql $(call agent_opt,N,-n) $(call agent_opt,CALLER,--caller) $(call agent_opt,CACHE,--cache) $(call agent_opt,VENDOR,--vendor)

agent-stages: ## Per-stage ms (auth, discover, creds, upstream, persist) + event-loop lag under load [C DURATION FUNCS=mod:Qual,...] ~1 min
	@$(AGENT) stages $(call agent_opt,C,--concurrency) $(call agent_opt,DURATION,--duration) $(call agent_opt,CALLER,--caller) $(call agent_opt,VENDOR,--vendor) $(call agent_opt,FUNCS,--funcs)

agent-cpu: ## py-spy at saturation: CPU by subsystem + hottest jentic_one file:line + flamegraph [C DURATION] ~1 min
	@$(AGENT) cpu $(call agent_opt,C,--concurrency) $(call agent_opt,DURATION,--duration) $(call agent_opt,CALLER,--caller) $(call agent_opt,VENDOR,--vendor)

agent-resilience: ## Fault scenarios x streaming/buffered: failures + broker error/warning logs grouped [SECONDS SCENARIOS] ~5 min
	@$(AGENT) resilience $(call agent_opt,SECONDS,--seconds) $(call agent_opt,SCENARIOS,--scenarios)

agent-compare: ## Diff two artifacts of one kind; exit 3 on regression [BASE=<json> HEAD=<json> TOLERANCE=0.1]
	@$(AGENT) compare --base $(BASE) $(call agent_opt,HEAD,--head) $(call agent_opt,TOLERANCE,--tolerance)
