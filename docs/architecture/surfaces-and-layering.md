# Surfaces and layering

How [`src/jentic_one/`](../../src/jentic_one/) is organized, and the import rules that keep it that
way. The rules are not aspirational: each one is pinned by a test in
[`tests/arch/`](../../tests/arch/), so a violating import fails CI.

## The five surfaces

Each surface is a package that owns its routes, services, and data access:

```
src/jentic_one/
├── registry/   # API catalog        (core/ ingest/ repos/ scoping/ services/ web/ pagination.py)
├── control/    # credentials layer  (core/ repos/ scoping/ services/ web/)
├── admin/      # operators & ops    (core/ repos/ scoping/ services/ web/)
├── auth/       # identity & tokens  (core/ repos/ services/ web/)
├── broker/     # execution plane    (adapters/ core/ repos/ services/ web/)
├── shared/     # cross-surface library code
├── mcp/        # the /mcp mount (installed via wiring, rides the control surface)
├── integrations/  # optional integrations (aws_marketplace license gate)
├── testing/    # public compliance bases (BaseBrokerComplianceTest et al.), shipped in the wheel
├── migrations/ # one Alembic env, three version trees
├── wiring.py   # the composition root — builds the cross-surface seams
└── __main__.py # process entrypoint
```

**Surfaces do not import each other**, with one sanctioned seam. Every
forbidden edge has a dedicated test in
[`tests/arch/test_module_boundaries.py`](../../tests/arch/test_module_boundaries.py)
(`test_broker_does_not_import_control`, `test_control_does_not_import_admin`,
…, `test_shared_does_not_import_auth`). The seam:
[`broker/services/credentials/`](../../src/jentic_one/broker/services/credentials/) imports control's OAuth provider and token
repo to refresh expired tokens during injection
([`broker/services/credentials/refresh.py`](../../src/jentic_one/broker/services/credentials/refresh.py)), and
`test_broker_does_not_import_control` excludes exactly that path. All other
cross-surface needs are met three ways:

- **[`shared/`](../../src/jentic_one/shared/)** — config, `Context`, the DB session layer, the `Broker`
  protocol, jobs, events, audit, scopes, telemetry. Its independence is
  enforced only in specific directions: `shared/` never imports `broker`,
  never imports `auth`, and never imports `admin.core.permissions`
  (`test_module_boundaries.py`). It does reach other surfaces where it
  assembles them — [`shared/web/app_factory.py`](../../src/jentic_one/shared/web/app_factory.py) imports registry's
  `ImportHandler`, [`shared/auth/verify.py`](../../src/jentic_one/shared/auth/verify.py) builds admin's
  `PermissionService`, and [`shared/release_check.py`](../../src/jentic_one/shared/release_check.py) reuses registry's
  catalog fetcher.
- **`wiring.py`** — the composition root, deliberately outside every surface
  package so it may import several of them. It builds the `AppContainer` and
  the in-process seams (for example `InProcessRegistryResolver`, which lets
  the broker resolve operations without importing `jentic_one.registry`).
- **Raw SQL at a named seam** — when the control plane must write an
  admin-DB row (approving an access request binds a toolkit to an agent),
  [`control/repos/effects_repo.py`](../../src/jentic_one/control/repos/effects_repo.py) uses raw SQL rather than importing admin's
  ORM models. The [worked example below](#a-request-layer-by-layer) traces
  this seam in action.

## The layers inside a surface

```
web/       FastAPI routers + dependencies. HTTP in, HTTP out.
services/  Use-cases. Own transactions and authorization decisions.
repos/     Data access. SQLAlchemy queries; auth-agnostic.
core/      Domain: ORM models (core/schema/), errors, pure logic.
scoping/   Row-level visibility filters (registry, control, admin only).
```

Dependencies point downward only: `web → services → repos → core`. The rules,
and the test that enforces each:

| Rule | Enforced by |
| ---- | ----------- |
| Web never touches the DB or SQLAlchemy | `test_web_layer.py::test_web_no_direct_db_imports` |
| Web never imports a repository | `test_web_layer.py::test_web_no_repository_imports` (and `test_web_handlers_use_services_not_repos`) |
| Handlers get `Context` via `Depends(get_ctx)`, never construct it | `test_web_layer.py::test_web_no_direct_context_construction` |
| Every non-health router declares an auth dependency | `test_web_layer.py::test_web_routers_require_auth` |
| Errors are RFC 9457 problem details, not `HTTPException` | `test_web_layer.py::test_web_uses_problem_details_not_http_exception` |
| Only `core/schema/` and `repos/` may import DB internals | `test_no_direct_db.py` |
| Repos are auth-agnostic (never import `Identity`) | `test_scoping_boundary.py::test_repos_do_not_import_identity` |
| A surface's `scoping/filters.py` sees only its own ORM models | `test_scoping_boundary.py::test_scoping_modules_only_import_own_surface_models` |
| Every scoped model is covered by its surface's filters | `test_scoping_coverage.py` |
| Admin ORM models inherit `AdminBase` only | `test_admin_base_usage.py` |
| Admin services never import SQLAlchemy | `test_admin_services_no_sqlalchemy.py` |
| Transactions via `DatabaseSession.transaction()`, no manual commit | `test_no_manual_commit.py` |
| One Alembic head per database tree | `test_migration_single_head.py` |

### The `scoping/` packages

[`registry/`](../../src/jentic_one/registry/), [`control/`](../../src/jentic_one/control/), and [`admin/`](../../src/jentic_one/admin/) each carry a `scoping/filters.py` whose
`build_access_filters(identity, model)` returns the WHERE clauses a repo
applies for row-level visibility: `org:admin` sees everything, an owner sees
their own rows, and an operator holding a delegation scope
(`owner:<resource>:read`) sees the rows of the agents they own. Services pass the
filters in; repos apply them; neither knows the other's internals. See
[identity and authorization](identity-and-authorization.md) for the scope
model these filters implement.

### A request, layer by layer

`POST /access-requests/{id}:decide` — an operator approving an agent's
access request — exercises every rule above, including the cross-database
seam:

1. **`web/`** — the router ([`control/web/routers/access_requests.py`](../../src/jentic_one/control/web/routers/access_requests.py))
   declares its auth dependency, receives the resolved `Identity`, converts
   the body to plain data, and calls `AccessRequestService.decide()`. No DB
   import, no business logic; a failure surfaces as an RFC 9457 problem
   detail.
2. **`services/`** — `decide()` builds the identity's access filters, opens
   `control_db.transaction()`, and applies the decision plus the
   control-side effects (credential→toolkit binds) **atomically** in that
   one transaction.
3. **`repos/`** — `AccessRequestRepository.get(session, id, filters=…)`
   applies the filters it was handed. It never sees the `Identity` that
   produced them.
4. **The cross-database seam** — an approved toolkit bind or scope grant
   must land in the *admin* DB, which the control transaction cannot span.
   So `decide()` commits phase 1, then drives the admin-DB writes through
   `EffectsRepository` (raw SQL, idempotent `ON CONFLICT`), and acks them
   back into the control DB. An un-acked item is the retry marker:
   re-calling `decide()` reconciles instead of erroring, so a crash between
   the two phases leaves no orphaned grants. This is the
   no-cross-database-foreign-keys rule (see [data model](data-model.md))
   showing up as control flow.

## Facade rules (one home per concern)

Several cross-cutting concerns are forced through a single module, each with
its own arch test:

| Concern | Single home | Test |
| ------- | ----------- | ---- |
| Encryption primitives (`cryptography`) | [`shared/crypto/encryption.py`](../../src/jentic_one/shared/crypto/encryption.py) | `test_encryption_facade.py` |
| JWKS key operations | [`shared/auth/jwks.py`](../../src/jentic_one/shared/auth/jwks.py) | `test_jwks_single_source.py` |
| Metrics exporters | [`shared/metrics.py`](../../src/jentic_one/shared/metrics.py) | `test_metrics_facade.py` |
| Tracing/OTel instrumentation | [`shared/tracing.py`](../../src/jentic_one/shared/tracing.py) | `test_tracing_facade.py` |
| Upstream HTTP transport | [`broker/adapters/runners/http.py`](../../src/jentic_one/broker/adapters/runners/http.py) (the `UpstreamRunner` seam) | `test_broker_runner_seam.py` |
| Structured logging (no stdlib `logging`) | [`shared/logging.py`](../../src/jentic_one/shared/logging.py) (structlog) | `test_no_stdlib_logging.py` |

The full `tests/arch/` suite also carries the drift guards for generated
artifacts (OpenAPI, endpoint tree, config schema/reference, skills, install
docs) — run `make test-arch` to execute everything.

## Related

- [Composition and processes](composition-and-processes.md) — how the
  packages above are assembled into running processes.
- [Broker execution](broker-execution.md) — the layering applied to the one
  surface that talks to the outside world.
