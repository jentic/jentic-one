# Generated reference

This folder holds **generated** API reference material. **Do not hand-edit these files** — they are regenerated from code and guarded by drift tests in CI.

## Files

| File | What it is | Regenerate with |
|---|---|---|
| [`endpoints.md`](endpoints.md) | Human-readable endpoint + permission tree, grouped by typical caller and surface. | `make endpoints` |
| `endpoints.json` | Portable, stack-agnostic machine-readable version of the same join (schema `jentic.endpoint-permission-tree/v1`). Safe to consume from other repos/sites/tools. | `make endpoints` |
| [`config.md`](config.md) | Every configuration key with its type, default, and `JENTIC__*` env var, generated from the `AppConfig` Pydantic model (the same source as [`config/config-schema.json`](../../config/config-schema.json)). | `make config-reference` |
| `mcp-tools.json` | The pinned MCP tool surface (names, descriptions, input schemas, annotations). The Go stdio server's `toolSpecs()` is the source of truth; [`cli/internal/cli/api/mcp_spec_test.go`](../../cli/internal/cli/api/mcp_spec_test.go) pins it here, the backend's `/mcp` mount loads it from the packaged copy, and [`tests/unit/mcp/test_tool_surface.py`](../../tests/unit/mcp/test_tool_surface.py) fails on any divergence. | `UPDATE_MCP_SPEC=1 go test ./internal/cli/api` (from [`cli/`](../../cli/)) |

A running server also serves this join live at **`GET /reference/endpoints.json`** (public), which is what the `jentic endpoints` CLI and the docs SPA consume. It returns the **same payload** as the committed `endpoints.json`: the offline `make endpoints` introspects a freshly-built broker app, while the live endpoint (mounted on the control-plane app, which never runs the separate broker process) supplies the broker's small static surface from a declaration in `endpoint_reference.py`. A drift test pins that declaration to the real broker app and asserts the live payload equals the committed file, so the two cannot diverge.

## How the endpoint + permission reference works

Every API operation is joined with the **permission(s)** it requires (the real gate) and an advisory **typical caller** hint:

- Permissions that gate at the FastAPI dependency (`get_current_identity(required_permissions=[...])`) are read directly from the route.
- Permissions enforced in the service layer (and actor-type inference) come from the curated map in [`src/jentic_one/shared/web/endpoint_permissions.py`](../../src/jentic_one/shared/web/endpoint_permissions.py).
- The grouping / _Typical caller_ column (`agent` / `operator` / `any`) is **advisory only**, inferred from the permission family. It is **not** an enforced restriction: access is gated by the permission, not the actor kind, so any actor holding the required permission can call the endpoint.
- **The OpenAPI specs model only authentication** (`BearerAuth` — an opaque bearer token); they do **not** carry per-operation permissions or actor types. OpenAPI's `security` model is built for OAuth2 scopes and can't faithfully express our authorization semantics (OR-of-permissions, the `org:admin` superuser bypass, the advisory typical-caller hint, and permissions enforced in the service layer rather than at the gateway), and a fabricated OAuth2 flow would misrepresent enforcement. So **this reference is the authoritative authorization source** — the committed files here and `GET /reference/endpoints.json`, which the CLI and docs SPA consume.

## Contributing (humans **and** agents)

1. To change a route's permission, prefer adding `required_permissions=[...]` to its `get_current_identity(...)` dependency so it is recovered automatically.
2. For service-layer-enforced routes, edit `PATH_PERMISSION_OVERRIDES` / `ACTOR_TYPE_OVERRIDES` in [`src/jentic_one/shared/web/endpoint_permissions.py`](../../src/jentic_one/shared/web/endpoint_permissions.py).
3. Run `make endpoints` **and** `make openapi`, then commit the code change together with the regenerated artifacts.

> Agents: treat `src/jentic_one/shared/web/endpoint_permissions.py` as the editable source of truth. Never hand-edit files in this folder.
