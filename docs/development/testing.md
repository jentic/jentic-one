# Testing

The Python test tiers and the generated-artifact drift checks. Set up the environment first: [Local development setup](local-setup.md).

## Python

Marks are declared in [`pyproject.toml`](../../pyproject.toml)
(`--strict-markers`, so an unknown mark fails collection).

| Tier | Command | Needs | Lives in |
| ---- | ------- | ----- | -------- |
| Unit | `make test-unit` (with coverage) | Nothing | `tests/unit/` |
| Unit + arch, fast | `make test-fast` (no coverage) | Nothing | `tests/unit/`, `tests/arch/` |
| Integration (Postgres) | `make test-integration` | Fixtures running: `make start-fixtures` | every `-m integration` test under `tests/` (`tests/integration/`, `tests/web/`, …) |
| Integration (SQLite) | `make test-integration-sqlite` | Nothing — temporary SQLite files per session | `tests/integration/` only, `JENTIC_TEST_BACKEND=sqlite` |
| Both backends | `make test-integration-all` | Fixtures running | — |
| Architecture | `make test-arch` | Nothing | `tests/arch/` — see [its README](../../tests/arch/README.md) |
| Smoke | `make test-smoke` | A running deployment | `tests/smoke/` |
| Perf / load | `make perf-help` | Docker; local only, never in CI | [`tests/perf/README.md`](../../tests/perf/README.md) |

Notes:

- Tests run in parallel (`pytest-xdist`, `-n auto --dist loadfile`). Run
  serially with `make <target> PYTEST_XDIST=`; append flags with
  `PYTEST_ARGS=...`. Each xdist worker gets its own Postgres database.
- Fixtures on a non-default port: set `JENTIC_PG_PORT` for both
  `make start-fixtures` and the test run.

UI tests: [`ui/README.md`](../../ui/README.md). Go CLI tests: `make help` in
[`cli/Makefile`](../../cli/Makefile).

## Generated artifacts and their drift checks

Never hand-edit these. Regenerate, then commit the code and the artifact
together.

| Artifact | Regenerate with | Drift check |
| -------- | --------------- | ----------- |
| `openapi/control/control.openapi.yaml`, `ui/openapi.json` | `make openapi` | `tests/arch/test_openapi_conformance.py` |
| UI client `ui/src/shared/api/generated/` | `cd ui && npm run codegen` (after `make openapi`) | CI job *Generated UI client up to date* |
| `ui/public/broker-openapi.json` (from the hand-written broker spec) | `make broker-reference` | `tests/arch/test_openapi_conformance.py` |
| CLI clients `cli/client/generated/` | `cd cli && make generate-api` | `make check-cli-gen`, `make check-vendored-specs` |
| `docs/reference/config.md` | `make config-reference` | `tests/arch/test_config_reference_conformance.py` |
| `config/config-schema.json` | `make config-schema` | `tests/arch/test_config_schema_conformance.py` |
| CLI installer config struct | `cd cli && make generate-config` | `make check-ctl-gen` |
| `docs/reference/endpoints.md` / `.json` | `make endpoints` | `tests/arch/test_endpoint_tree.py` |
| `ui/public/cli-reference.json` | `make cli-reference` | CI step *CLI reference up to date* in the Go job |
| Served skill copies | `make skills` | `tests/arch/test_skill_drift.py` |
| `docs/reference/mcp-tools.json` (MCP tool surface) | `cd cli && UPDATE_MCP_SPEC=1 go test ./internal/cli/api -run TestMCPToolSurfaceSpec`, then copy it to `src/jentic_one/mcp/_spec/mcp-tools.json` | the Go test itself, and `tests/unit/mcp/test_tool_surface.py` (the two copies must be byte-identical) |
| `.secrets.baseline` | `make secrets-baseline` | `make detect-secrets` |

An `AppConfig` change needs three of these: `make config-reference`,
`make config-schema`, and `cd cli && make generate-config`.
