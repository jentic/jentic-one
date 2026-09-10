# Repository tooling

The CLI source installer and the generators behind the repo's generated
references. Each generator is invoked through its `make` target — never run
by hand into a different path.

| Tool | Make target | What it produces |
| ---- | ----------- | ---------------- |
| [`install.sh`](install.sh) | — | Builds `jenticctl` + `jentic` from source onto your PATH; see [`install-README.md`](install-README.md) |
| [`config_reference.py`](config_reference.py) | `make config-reference` | [`docs/reference/config.md`](../docs/reference/config.md) from the `AppConfig` model |
| [`config_schema_export.py`](config_schema_export.py) | `make config-schema` | [`config/config-schema.json`](../config/config-schema.json) (also vendored into the CLI) |
| [`endpoint_tree.py`](endpoint_tree.py) | `make endpoints` | [`docs/reference/endpoints.md`](../docs/reference/endpoints.md) + [`endpoints.json`](../docs/reference/endpoints.json) |
| [`openapi_export.py`](openapi_export.py) | `make openapi` | `openapi/control/control.openapi.yaml` + `ui/openapi.json` from code |
| [`broker_reference.py`](broker_reference.py) | `make broker-reference` | `ui/public/broker-openapi.json` from the hand-curated broker spec |
| [`openapi_parity.py`](openapi_parity.py) | `make openapi-parity` | Coverage report: reference vs generated OpenAPI specs |
| [`skills_sync.py`](skills_sync.py) | `make skills` | Mirrors [`skills/`](../skills/) into its served `content/` copies |
| [`deploy/`](deploy/) | — | Click-based deploy CLI replacing the k8s/Helm make targets: `uv run python -m tools.deploy` |

Each generated artifact has a byte-equality gate in
[`tests/arch/`](../tests/arch/README.md), so a stale artifact fails CI rather
than drifting.
