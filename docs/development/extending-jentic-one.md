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
| `register_catalog_search_strategy` *(provisional)* | `jentic_one.registry.services.catalog.search` | Register a `CatalogSearchStrategy` (class or instance) that ranks `GET /catalog?q=` results; select it with `catalog.search_strategy`. See [Catalog search strategies](#catalog-search-strategies-provisional). |
| `jentic_one.testing` | `jentic_one.testing` | Compliance base classes that prove your implementations honor the seam contracts. |
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

#### Catalog search strategies (provisional)

> **Provisional:** this seam may change before 1.0. Pin the `jentic-one`
> version your extension is built against.

`GET /catalog?q=` (and the MCP `search_catalog` tool) rank results through a
`CatalogSearchStrategy`. The built-in `token` strategy is the default; an
extension can register another and select it with `catalog.search_strategy`.
The service keeps filtering, validation, ordering, paging and cursors, so a
strategy only ranks:

```python
from jentic_one.registry.services.catalog.search import (
    CatalogMatch,
    CatalogSearchRequest,
    RankedEntry,
    register_catalog_search_strategy,
)
from jentic_one.shared.config import AppConfig, CatalogConfig


class MyRanker:
    name = "my_ranker"  # ^[a-z][a-z0-9_]{0,31}$, recorded in cursors

    @classmethod
    def from_config(cls, config: CatalogConfig, *, app_config: AppConfig) -> "MyRanker":
        return cls()  # read your own section via app_config.extension("my_ext")

    async def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
        return [
            RankedEntry(entry=e, score=1.0, match=CatalogMatch(("alias",)))
            for e in request.entries
            if request.q.lower() in e.api_id
        ]


register_catalog_search_strategy(MyRanker)  # a class, or a ready instance
```

The contract:

- **Registration.** Register at import time, before the app is assembled. A
  class is built once per booted config (through `from_config` when it defines
  it) and the instance is shared across requests. App assembly fails with a
  `ConfigError` when `catalog.search_strategy` is not registered or can't be
  built. `rank` must be `async`. A name can't be re-registered by a different
  object. `from_config` runs synchronously while the app is assembled, before
  the event loop serves requests: create network clients (HTTP sessions,
  connection pools) lazily on the first `rank()` call.
- **Input.** `rank()` only sees searches (queries with at least one token);
  `request.entries` is already filtered. Return the **full** ranked list — never
  apply a limit. `request.options` is empty today; new request fields are added
  with defaults.
- **Output.** Higher scores rank first. The service drops rows with a
  non-finite or non-numeric score, rows for entries outside `request.entries`
  and lower-scoring duplicates, then sorts by `(-score, api_id)`. A non-empty
  result in which no row is valid counts as a failure. `match.channels` must be
  slugs (`^[a-z][a-z0-9_-]{0,31}$`, at most 8); others are dropped.
- **Stability.** Each page re-ranks and resumes after the cursor's exact
  `(score, api_id)`, so scores must be identical across calls for the same
  request. `rank()` runs once per page. If ranking is expensive, cache
  **per-entry scores** keyed by `(q, request.manifest_revision, api_id)` and
  rank `request.entries` from that map. Never cache the ranked list:
  `request.entries` also depends on the list filters and on which APIs are
  registered or outdated, so two requests with the same `q` and revision can
  carry different candidates.
- **Deadline.** `catalog.search_deadline_ms` (default 300) bounds each call;
  cancellation only interrupts an `await`, so offload CPU-bound work (for
  example `asyncio.to_thread`). Set the deadline well above the strategy's tail
  latency.
- **Failure.** Raise to fail. On the first page the walk falls back to `token`
  (logged and counted). On a later page the response is 503
  `catalog_search_unavailable` with `Retry-After: 1`: the client retries the
  page, or restarts the search without a cursor.
- **Cursors.** A cursor records its walk's strategy (key `st`). A later page is
  served only while that strategy is the configured one: any other strategy in
  the cursor answers 400 (restart the search). A version suffix on `st` is not
  defined yet; a build that predates one would answer such a cursor with 400,
  so clients restart the search.
- **Rollout.** App assembly refuses a configured strategy that isn't
  registered, so roll out in two steps:
  1. Ship a build that registers the strategy on every replica while
     `catalog.search_strategy` stays `token`.
  2. Set `catalog.search_strategy` to the new name. While that config change
     rolls out, replicas still configured with `token` answer 400 to cursors
     from the new strategy and clients restart the search; `token` cursors
     keep working everywhere.

  To go back, reverse the order: set `catalog.search_strategy` to `token`
  first (open walks get a 400 and restart), then deploy a build without the
  strategy. A 503 means the configured strategy failed on a later page; the
  503 for a strategy that isn't registered in the process is a safety net that
  the boot check normally makes unreachable.
- **Tests.** Use the `registered_catalog_search_strategy(strategy)` context
  manager to register a strategy for one test without leaking it.

Failures are counted by `jentic_one.catalog_search.strategy_failures` (see
[Monitoring](../operations/monitoring.md#metrics-and-traces)).

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

### 4. (Optional) Compose your own CLI binary

The Go CLI exposes an importable `pkg/core` container + root builder. Build your
own container with `ExtraCommands` and call `core.NewRootCmd`; the built-in
command tree is assembled by `internal/cmd` (the `internal/cmd → pkg/core` edge
stays one-directional, so an alternate binary composes its own tree without an
import cycle). Migration ordering is **not** modelled in Go — the CLI only
invokes the Python runner, which owns `DB_TARGETS` and its upgrade/rollback
order.

## Unknown config keys fail loudly

`AppConfig` sets `model_config = ConfigDict(extra="forbid")`. An **unrecognized
top-level config key** — one that is neither a core field nor a *registered*
extension section — causes a **loud failure at startup** rather than being
silently ignored. This ensures extension sections are formally registered via
`register_config`: a typo'd or unregistered top-level key stops the app instead
of dropping your configuration on the floor.
