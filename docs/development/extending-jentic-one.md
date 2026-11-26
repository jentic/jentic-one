# Extending Jentic One

`jentic-one` ships a set of **backward-compatible seams** so an integrator can
inject alternate implementations and mount extra components **without editing
core code**. When no integrator wiring is supplied, behavior is identical to the
stock distribution.

This guide is the unified composition story: how the seams fit together and the
order in which an integrator wires them. Each seam is also documented at its
definition — this page links them into one workflow. For the concepts the seams
plug into (the surfaces, the `AppContainer` composition root, the broker's
"one pipeline, two callers" rule), see [docs/architecture/](../architecture/README.md).

## The seams at a glance

| Seam | Where | What it lets you do |
| ---- | ----- | ------------------- |
| `AppContainer` | `jentic_one.shared.web.container` | Inject a `Broker`; mount extra routers/installers after the built-in surfaces. |
| `AppContainer.unregistered_url_handler` | `jentic_one.shared.web.container` (contract: `jentic_one.shared.web.protocols.UnregisteredUrlHandler`) | Intercept broker traffic to unregistered METHOD+URLs at the sync web edge: return a `Response` to short-circuit, or `None` for today's 404. Fires only on the standalone broker app — wire it via `jentic_one.broker.web.app.create_app`. |
| `register_config` | `jentic_one.shared.config` | Add a top-level config section validated by your own pydantic model. |
| `register_target` | `jentic_one.migrations.targets` | Add an isolated migration target to the ordered upgrade/rollback sequence. |
| `register_telemetry_event` | `jentic_one.shared.telemetry.events` | Forward extra telemetry events without editing the closed enum. |
| `register_strategy` | `jentic_one.registry.repos.search.registry` | Register a `SearchStrategy` under a new `(dialect, search_mode)` pair. Declare an optional `from_config(config, *, app_config=None)` classmethod (`ConfigurableSearchStrategy`) to receive the active `SearchConfig` and the booted `AppConfig` at resolve time; raise `SearchUnsupportedError` (resolve or query time) for the 501 `search_unsupported` problem. |
| `resolve_strategy_by_name` | `jentic_one.registry.repos.search` | Composition seam: a composite strategy (e.g. a hybrid fusing lexical + semantic) resolves its children by `(dialect, name)` instead of importing their modules, so it uses whatever is registered under that key. Raises `SearchUnsupportedError` naming the available modes when the key is unknown. |
| `ACTIVE_REVISION_STATES` / `search_filter_clause` | `jentic_one.registry.repos.search` | The shared pin/active-state/api-filter contract every strategy applies, so composite strategies fuse filter-identical result sets. Use `search_filter_clause(api_filters=..., revision_pins=...)` on a statement that joins `ApiRevision`; hand-written SQL builds its state predicate from `ACTIVE_REVISION_STATES`. |
| `register_pipeline_stage` | `jentic_one.registry.ingest.pipeline.stage_registry` | Append extra ingest stages that run after the built-in pipeline, inside the same transaction. |
| `register_state_backend` | `jentic_one.shared.state` | Register a shared-state backend (rate-limit buckets, circuit counters, `Idempotency-Key` records, auth single-use markers) under a name operators select with `state.backend`. The builder receives a `StateBackendBuildContext` (the `state:` section, whose free-form `options` mapping it validates itself, and the process's surface set) and returns a `SharedStateBackend`. The process builds one backend at app assembly, shares it across every surface it serves, and closes it once at shutdown; an unregistered name fails startup listing the registered ones. `memory` (the default) and `redis` (deprecated) are built in. Prove it with `BaseStateBackendContract`. |
| `register_access_filter_provider` | `jentic_one.control.scoping.filters` | OR an extra read-only visibility clause into control-surface read filters (`include_shared=True` call sites only; writes stay owner-scoped). Any table the clause references must be readable by the control DB role. Inspect registered providers via `access_filter_providers()`. |
| `register_user_deleted_hook` | `jentic_one.admin.services.user_hooks` | Run an async `fn(ctx, user_id, identity)` after a user delete commits, e.g. to clean up the extension's own per-user rows. Hooks run inside the delete request, each bounded by `USER_DELETED_HOOK_TIMEOUT_S` (10 s). Best effort, not durable: a hook that raises or times out is logged, counted (`admin.user_deleted_hook.failures`) and skipped, and nothing retries it automatically. Hooks must be idempotent: deleting an already-deleted user runs them again. |
| `jentic_one.composition` | `jentic_one.composition` | Public composition entrypoints (`build_app`, `build_context`, `expand_allowed_dbs`) that assemble a `Context` and `FastAPI` app through the same path `jentic-one serve` uses. `build_app(ctx, apps, container=...)` uses your `AppContainer` for the combined app (any `apps` list of two or more surfaces) and for a standalone `control`, `auth` or `broker` process, and still runs the composition root's post-build wiring (such as the broker's registry resolver). A standalone `registry` or `admin` process ignores `container` and builds its default wiring. |
| Broker caller identity | `jentic_one.broker.web.deps.require_broker_identity` (validator: `app.state.broker_token_validator`) | Authenticate a request on an extra router mounted on the standalone broker exactly as the broker's own routes do: the same `jak_` keys, opaque `at_` tokens and trusted-issuer JWTs, with the same caches and revocation. Use the dependency, or call `await app.state.broker_token_validator.validate(credential)` (returns an `Identity`, raises `TokenValidationError`). |
| `jentic_one.control.credentials_seam` | `jentic_one.control.credentials_seam` (drift guard: `tests/arch/test_credentials_seam_public_api.py`) | Read a stored credential's header (`get_credential_header`) and decrypted material (`resolve_credential_material`, **no authorization: check the caller first**), and create, update and delete credentials your extension manages (`create_managed_credential`, `update_managed_credential`, `delete_managed_credential`). Re-exports the payload types, `SigV4Material`/`sign_request` and the `Credential` ORM class, so an extension imports nothing internal. See [Managed credentials](#managed-credentials). |
| `registerManagedByRenderer` | `ui/src/shared/credentials/components` (`managedBy.tsx`) | Render the badge for credentials managed by your extension's kind; unregistered kinds show "Managed by <kind>". See [Managed credentials](#managed-credentials). |
| `jentic_one.testing` | `jentic_one.testing` | Compliance base classes that prove your implementations honor the seam contracts. |
| `jentic_one.testing.introspect` | `jentic_one.testing.introspect` | Read-only route and scoping introspection (`build_app`, `route_facts`, `scoped_tables`, `access_filter_providers`, `RouteFact`, `ScopedTable`, `INTROSPECTION_SCHEMA`) for downstream architecture and security gates. |
| `pkg/core.AppContainer` | `cli/pkg/core` (Go) | Compose your own CLI binary with extra command groups. |

All Python registries are process-global and populated **at import time** (e.g.
in your package's `__init__`) — call the `register_*` functions before
`load_config()` / app construction runs.

## Composition workflow

An integrator's composition root typically does the following, in order.

### 1. Register config, migration targets, and telemetry events at import time

```python
# my_ext/__init__.py
from pydantic import BaseModel

from jentic_one.migrations.targets import MigrationTarget, register_target
from jentic_one.shared.config import register_config
from jentic_one.shared.db.base import RegistryBase  # or your own declarative base
from jentic_one.shared.telemetry.events import register_telemetry_event


class MyExtConfig(BaseModel):
    enabled: bool = False
    endpoint: str = "https://example.internal"


# A new top-level YAML/env section: `my_ext:` in jentic-one.yaml.
# Collision guards reject names that shadow a core field (e.g. "broker") or the
# reserved "extensions" key.
register_config("my_ext", MyExtConfig)

# An isolated migration target. Insertion order is the canonical UPGRADE order;
# rollback reverses it. Register after the built-ins so it upgrades last.
register_target(MigrationTarget("my_ext", RegistryBase.metadata))

# Forward an extra telemetry event. Wire names are lower_snake_case; collisions
# with the built-in enum/map are rejected so a typo can't silently drop events.
register_telemetry_event("my_ext.thing_happened", "thing_happened")
```

Read your validated config back with `AppConfig.extension("my_ext")`, which
returns your model instance (or `None` if the section is absent).

You can also append **ingest pipeline stages**. Registered stages run after
every built-in stage (in registration order; when several packages register
stages, cross-package order is their import order — register from one place if
relative order matters), see the full context bag the built-ins produced
(`operation_ids`, `revision_id`, …), and share the ingest transaction — a
failing extension stage rolls back the whole ingest. Gate yourself on your own
config section; the registry is config-blind:

```python
import uuid

from jentic_one.registry.ingest.pipeline import (
    PipelineContext,
    PipelineStageSpec,
    register_pipeline_stage,
)
from jentic_one.registry.ingest.stages import BasePipelineStage


class EmbedOperationsStage(BasePipelineStage):
    _requires = {"operation_ids": set, "revision_id": uuid.UUID}

    async def _run(self, ctx: PipelineContext) -> None:
        cfg = ctx.config.extension("my_ext") if ctx.config else None
        if cfg is None or not cfg.enabled:
            return  # feature-gated: registered unconditionally, no-op when off
        ...  # embed ctx.require("operation_ids", set) on ctx.session


register_pipeline_stage(
    PipelineStageSpec(name="my_ext.embed_operations", factory=EmbedOperationsStage)
)
```

A **shared-state backend** is registered the same way. OSS supports one
process per surface on the in-process `memory` backend; a backend registered
here is how a deployment shares rate limits, circuit state and idempotency
records across several processes of one surface:

```python
from pydantic import BaseModel

from jentic_one.shared.state import (
    SharedStateBackend,
    StateBackendBuildContext,
    register_state_backend,
)


class ClusterOptions(BaseModel):
    url: str
    key_prefix: str = "jentic:"


def build_cluster_backend(ctx: StateBackendBuildContext) -> SharedStateBackend:
    # state.options is passed through unvalidated: validate it here, so a bad
    # value fails startup rather than the first request.
    opts = ClusterOptions.model_validate(ctx.config.options)
    # One prefix for every process of a deployment, so replicas of a surface
    # (and different process shapes serving it) share the same keys.
    return MyClusterBackend(opts.url, key_prefix=opts.key_prefix)


register_state_backend("my-cluster", build_cluster_backend)
```

Operators select it with `state.backend: my-cluster` (settings under
`state.options`, or `JENTIC__STATE__OPTIONS__URL`). `state.options` is a plain
mapping, so the config never redacts it: type a credential in it as
`SecretStr` in your options model and keep it out of committed config files
(set it with a `JENTIC__STATE__OPTIONS__*` env var). The backend must implement
every `SharedStateBackend` method atomically across processes; subclass
`BaseStateBackendContract` (step 3) to prove it.

### 2. Build an `AppContainer` and compose the app

Start from the default container and add your `Broker` and any extra
routers/installers. Extra routers and installers run **after** all built-in
surfaces, so they mount last and never shadow a built-in route. The exception
is a surface's root `/{name:path}` catch-all (the standalone broker's forward
proxy), which `create_surface_app` includes after the extensions so it never
shadows an extra router either.

```python
from fastapi import APIRouter

from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.container import AppContainer

from my_ext.broker import MyBroker

my_router = APIRouter()  # your extra routes


def build_app(ctx: Context):
    container = AppContainer(
        ctx=ctx,
        broker=MyBroker(...),  # injected data-plane broker
        extra_routers=[(my_router, "/my-ext", ["my-ext"])],
        extra_installers=[lambda app, ctx: ...],  # runs against the root app last
    )
    return create_combined_app(ctx, ctx.config.apps, container=container)
```

The container stashes your `broker` on `app.state.broker`. It is honored by
**both** callers of the "one pipeline, two callers" seam — the sync router
([`broker/web/routers/execute.py`](../../src/jentic_one/broker/web/routers/execute.py)) and the async worker
(`PipelineExecutor`) — so an injected broker reaches the sync **and** async
paths, not just one of them.

> **Resilience tradeoff.** An injected `Broker` **owns its own transport and
> resilience** (circuit breaking, connection pooling, retries, per-host
> bulkheads, timeouts). It deliberately opts out of the built-in resilience
> stack that wraps the *default* broker's runner. If you only want to
> observe/enrich the standard path rather than replace transport, wrap a
> `DefaultBroker` and delegate to it so the built-in stack is retained. See the
> `Broker` protocol docstring in `jentic_one.shared.broker.broker`.

> **Unregistered-URL tradeoff.** An `unregistered_url_handler` runs *instead
> of* the registered-operation pipeline, so **none** of the built-in controls
> apply: no PBAC evaluation, no credential injection, no body/response-size
> caps, no transfer deadlines, no resilience stack, and no execution record —
> core emits only the outcome-attributed `broker.unregistered_url.handled`
> counter (`handled`/`declined`/`error`). The only controls that have run are
> authentication, the per-actor execute rate limit, and the egress
> **pre-flight** check — connection-time DNS pinning has **not**, so a
> forwarding implementation must fetch through `HttpClientProvider` and only
> to the validated URL it receives. Via `request.app.state` the handler
> reaches the full deployment context (DB sessions, key material, config):
> it is fully trusted deployment code, not a sandboxed plugin. A handler
> exception (other than a deliberate `ProblemDetailException`) is logged and
> counted by core and falls through to the standard 404. See the
> `UnregisteredUrlHandler` docstring in `jentic_one.shared.web.protocols` for
> the full contract.

The `unregistered_url_handler` seam needs a different composition: the hook
fires only in the broker's catch-all, and the broker runs as the **sole
surface** of its process (`jentic_one.__main__` refuses to bundle it), so a
handler wired into a combined app can never fire (the factory logs a
`unregistered_url_handler_unreachable` warning). Compose the broker app
directly, and install the registry resolver its catch-all resolves URLs
through:

```python
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.shared.context import Context
from jentic_one.shared.web.container import AppContainer
from jentic_one.wiring import install_broker_registry_resolver

from my_ext.monitor import MyUnregisteredUrlHandler


def build_broker_app(ctx: Context):
    container = AppContainer(
        ctx=ctx,
        # Intercept traffic to unregistered METHOD+URLs (see the
        # "Unregistered-URL tradeoff" note above).
        unregistered_url_handler=MyUnregisteredUrlHandler(...),
    )
    app = create_broker_app(ctx, container=container)
    # The catch-all resolves URLs via app.state.broker_registry_resolver;
    # without it every execute request fails before discovery.
    install_broker_registry_resolver(app, ctx)
    return app
```

#### Extra routers on the standalone broker

To serve your own routes from the broker process (next to its forward proxy),
put them on the container and build the app through `jentic_one.composition`.
`build_app` hands the container to the broker's factory and installs the
registry resolver itself, so it replaces the manual wiring above:

```python
from fastapi import APIRouter, Depends

from jentic_one.broker.web.deps import require_broker_identity
from jentic_one.composition import build_app, build_context
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import load_config
from jentic_one.shared.web.container import AppContainer

my_router = APIRouter()


@my_router.get("/status")
async def status(identity: Identity = Depends(require_broker_identity)) -> dict[str, str]:
    return {"caller": identity.sub}


config = load_config()
ctx = build_context(config, ["broker"])
container = AppContainer(ctx=ctx, extra_routers=[(my_router, "/my-ext", ["my-ext"])])
app = build_app(ctx, ["broker"], container=container)
```

Starlette matches routes in registration order and the broker's root
`/{upstream_url:path}` catch-all matches every path, so `create_surface_app`
includes the catch-all **after** the container's extra routers: `/my-ext/status`
answers from your router, and every other path still reaches the proxy. Give
your routes a prefix that no upstream host can collide with, because a path
you claim is no longer proxied.

`require_broker_identity` reads the credential the same way the broker's
routes do (the `x-jentic-api-key` header, then `Authorization: Bearer`) and
validates it with `app.state.broker_token_validator`, the broker's composite
validator (agent API keys, opaque access tokens and trusted-issuer JWTs,
behind short-TTL caches the broker invalidates on revocation). A missing or
invalid credential answers the standard 401 problem. To validate a credential
you hold outside a FastAPI dependency (for example one forwarded in another
header), call the validator directly:

```python
from jentic_one.shared.auth.errors import TokenValidationError

try:
    identity = await request.app.state.broker_token_validator.validate(credential)
except TokenValidationError:
    ...  # refuse: unknown, revoked, expired or inactive
```

The validator exists only on the standalone broker app; control-plane
surfaces verify callers with their own `app.state.verify_token` chain.

### 3. Prove your implementations comply with the seam contracts

`runtime_checkable` Protocols only validate method *presence* — an
implementation can drift in parameter names, defaults, or return type and still
pass `isinstance`. Subclass the `jentic_one.testing` compliance bases in your own
test suite to also assert the exact `inspect.signature` of every seam method:

```python
# my_ext/tests/test_compliance.py
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.web.protocols import UnregisteredUrlHandler
from jentic_one.testing import (
    BaseBrokerComplianceTest,
    BaseSearchStrategyComplianceTest,
    BaseUnregisteredUrlHandlerComplianceTest,
)

from my_ext.broker import MyBroker
from my_ext.monitor import MyUnregisteredUrlHandler
from my_ext.search import MyStrategy


class TestMyBrokerCompliance(BaseBrokerComplianceTest):
    def broker_factory(self) -> Broker:
        return MyBroker(...)


class TestMyStrategyCompliance(BaseSearchStrategyComplianceTest):
    strategy_cls = MyStrategy
    # Override strategy_factory() when MyStrategy() is not zero-arg (e.g. it is
    # built through from_config); a declared from_config is signature-checked.


class TestMyHandlerCompliance(BaseUnregisteredUrlHandlerComplianceTest):
    def handler_factory(self) -> UnregisteredUrlHandler:
        return MyUnregisteredUrlHandler(...)
```

These `Test*` subclasses are collected by pytest and fail loudly if your
implementation diverges from the built-in contract — the same guard the OSS
suite runs against its own defaults ([`tests/unit/testing/test_compliance_oss.py`](../../tests/unit/testing/test_compliance_oss.py)).

A shared-state backend also has a behavioural contract: claims are won once,
counters keep their first deadline, buckets refill at their rate, entries
expire. `BaseStateBackendContract` runs it against a fresh backend per test
(its tests are `async def`, so run them under pytest-asyncio):

```python
import uuid

from jentic_one.shared.state import SharedStateBackend
from jentic_one.testing import BaseStateBackendContract


class TestMyClusterBackendContract(BaseStateBackendContract):
    def backend_factory(self) -> SharedStateBackend:
        return MyClusterBackend(TEST_URL, key_prefix=f"test:{uuid.uuid4().hex}:")

    # Expiry and refill tests call advance(); it sleeps by default. Override it
    # to move an injected clock instead, as the memory backend's suite does
    # (tests/unit/shared/state/test_state_backend_contract.py).
```

To gate route authentication, permission wiring, and ORM scoping coverage in a
downstream architecture suite without importing private `_` symbols, use
`jentic_one.testing.introspect`:

```python
from jentic_one.testing.introspect import (
    access_filter_providers,
    build_app,
    route_facts,
    scoped_tables,
)

app = build_app(["control", "admin", "auth", "registry"])
facts = route_facts(app)
tables = scoped_tables()
providers = access_filter_providers()
```

### 4. (Optional) Compose your own CLI binary

The Go CLI exposes an importable `pkg/core` container + root builder. Build your
own container with `ExtraCommands` and call `core.NewRootCmd`; the built-in
command tree is assembled by `internal/cmd` (the `internal/cmd → pkg/core` edge
stays one-directional, so an alternate binary composes its own tree without an
import cycle). Migration ordering is **not** modelled in Go — the CLI only
invokes the Python runner, which owns `DB_TARGETS` and its upgrade/rollback
order.

## Managed credentials

An extension that keeps credentials of its own (for example, one per object it
manages) stores them as ordinary credentials **managed by** that object, through
`jentic_one.control.credentials_seam`. Its routes and services never import
`jentic_one.control.core`, `repos` or `services`.

```python
from jentic_one.control.credentials_seam import (
    APIReference,
    CredentialCreate,
    CredentialType,
    CredentialUpdate,
    ManagedBy,
    create_managed_credential,
    delete_managed_credential,
    resolve_credential_material,
    update_managed_credential,
)

marker = ManagedBy(kind="my_ext_connection", ref=connection_id)

header = await create_managed_credential(
    ctx,
    CredentialCreate(
        type=CredentialType.API_KEY,
        name="Upstream key",
        api=APIReference(vendor="api.example.com", name="", version=""),
        key=secret,
        location="header",
        field_name="X-Api-Key",
    ),
    identity=identity,      # becomes the credential's owner (created_by)
    managed_by=marker,
)

# Rotate in place: same id, audited with `key_rotated`, broker caches invalidated.
await update_managed_credential(
    ctx, header.id, CredentialUpdate(type=CredentialType.API_KEY, key=new_secret),
    identity=identity, managed_by=marker,
)

# Use it — only after your own authorization decided the caller may.
material = await resolve_credential_material(
    ctx, header.id, expected_type=CredentialType.API_KEY
)

await delete_managed_credential(ctx, header.id, identity=identity, managed_by=marker)
```

The marker (`ManagedBy(kind, ref)`, columns `credentials.managed_by_kind` and
`managed_by_ref`) is **set only at creation, only through the seam, and never
changes**. No HTTP request model accepts it; a `managed_by` field in a
`POST /credentials` body is ignored. API responses show it read-only as
`managed_by: {kind, ref} | null`. It is unrelated to the provider-level `managed`
flag of `GET /credentials/providers` (Pipedream-style providers).

A managed credential:

- **cannot be bound to an agent**: `POST /agents/{id}/credentials` returns
  `409 credential_managed`, it is never offered as an existing credential in a
  connect session, and the UI binding pickers leave it out;
- **is never resolved by the broker for an agent call**, even when named with
  `Jentic-Credential-Id` (that resolves as not found); the extension reads the
  material through the seam instead;
- **is changed only by its extension**: `PATCH` and `DELETE /credentials/{id}`
  return `409 credential_managed`, except that an `org:admin` may still `DELETE`
  it (the way out when its managing object is gone). The seam's update and
  delete refuse a marker the credential does not carry (`CredentialManagedError`).

Create, update and delete run through the same `CredentialService` as the HTTP
API: validation, owner scoping (a non-admin `identity` may change only the
credentials it created), the audit records (the marker is in the snapshots; a
secret rotation adds `*_rotated` markers, never the value) and broker cache
invalidation are identical.

**`resolve_credential_material` performs no authorization.** It returns the
plaintext of any `active`, `connected` credential of the expected type
(an API key credential gives `ApiKeyMaterial`, a SigV4 one `SigV4Material`; both elide secrets in
`repr`), and raises `CredentialUnavailableError` for a missing, inactive, not
connected, wrong-type or undecryptable credential. It is a Python API with no
route. The seam never caches or logs the plaintext; do not cache it either.
`CredentialHeader.revision` (the row's `updated_at`) moves on every persisted
change, including a rotation, so it can key a cache of anything you derive from
the secret.

In the UI, register a badge for your kind once, at import time:

```tsx
import { registerManagedByRenderer } from '@/shared/credentials/components';

registerManagedByRenderer('my_ext_connection', ({ managedBy }) => (
    <MyConnectionLink connectionId={managedBy.ref} />
));
```

The credential cards show it; an unregistered kind shows the fallback
"Managed by my ext connection". The edit sheet and the delete confirm (for a
non-admin) show where the credential is managed instead of a form.

## Unknown config keys fail loudly

`AppConfig` sets `model_config = ConfigDict(extra="forbid")`. An **unrecognized
top-level config key** — one that is neither a core field nor a *registered*
extension section — causes a **loud failure at startup** rather than being
silently ignored. This ensures extension sections are formally registered via
`register_config`: a typo'd or unregistered top-level key stops the app instead
of dropping your configuration on the floor.
