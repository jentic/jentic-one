# Extending jentic-one

`jentic-one` ships a set of **backward-compatible seams** so an integrator can
inject alternate implementations and mount extra components **without editing
core code**. When no integrator wiring is supplied, behavior is identical to the
stock distribution.

This guide is the unified composition story: how the seams fit together and the
order in which an integrator wires them. Each seam is also documented at its
definition — this page links them into one workflow.

## The seams at a glance

| Seam | Where | What it lets you do |
| ---- | ----- | ------------------- |
| `AppContainer` | `jentic_one.shared.web.container` | Inject a `Broker`; mount extra routers/installers after the built-in surfaces. |
| `register_config` | `jentic_one.shared.config` | Add a top-level config section validated by your own pydantic model. |
| `register_target` | `jentic_one.migrations.targets` | Add an isolated migration target to the ordered upgrade/rollback sequence. |
| `register_telemetry_event` | `jentic_one.shared.telemetry.events` | Forward extra telemetry events without editing the closed enum. |
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

### 2. Build an `AppContainer` and compose the app

Start from the default container and add your `Broker` and any extra
routers/installers. Extra routers and installers run **after** all built-in
surfaces, so they mount last and never shadow a built-in route.

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
        broker=MyBroker(...),                      # injected data-plane broker
        extra_routers=[(my_router, "/my-ext", ["my-ext"])],
        extra_installers=[lambda app, ctx: ...],   # runs against the root app last
    )
    return create_combined_app(ctx, ctx.config.apps, container=container)
```

The container stashes your `broker` on `app.state.broker`. It is honored by
**both** callers of the "one pipeline, two callers" seam — the sync router
(`broker/web/routers/execute.py`) and the async worker
(`PipelineExecutor`) — so an injected broker reaches the sync **and** async
paths, not just one of them.

> **Resilience tradeoff.** An injected `Broker` **owns its own transport and
> resilience** (circuit breaking, connection pooling, retries, per-host
> bulkheads, timeouts). It deliberately opts out of the built-in resilience
> stack that wraps the *default* broker's runner. If you only want to
> observe/enrich the standard path rather than replace transport, wrap a
> `DefaultBroker` and delegate to it so the built-in stack is retained. See the
> `Broker` protocol docstring in `jentic_one.shared.broker.broker`.

### 3. Prove your implementations comply with the seam contracts

`runtime_checkable` Protocols only validate method *presence* — an
implementation can drift in parameter names, defaults, or return type and still
pass `isinstance`. Subclass the `jentic_one.testing` compliance bases in your own
test suite to also assert the exact `inspect.signature` of every seam method:

```python
# my_ext/tests/test_compliance.py
from jentic_one.shared.broker.broker import Broker
from jentic_one.testing import BaseBrokerComplianceTest, BaseSearchStrategyComplianceTest

from my_ext.broker import MyBroker
from my_ext.search import MyStrategy


class TestMyBrokerCompliance(BaseBrokerComplianceTest):
    def broker_factory(self) -> Broker:
        return MyBroker(...)


class TestMyStrategyCompliance(BaseSearchStrategyComplianceTest):
    strategy_cls = MyStrategy
```

These `Test*` subclasses are collected by pytest and fail loudly if your
implementation diverges from the built-in contract — the same guard the OSS
suite runs against its own defaults (`tests/unit/testing/test_compliance_oss.py`).

### 4. (Optional) Compose your own CLI binary

The Go CLI exposes an importable `pkg/core` container + root builder. Build your
own container with `ExtraCommands` and call `core.NewRootCmd`; the built-in
command tree is assembled by `internal/cmd` (the `internal/cmd → pkg/core` edge
stays one-directional, so an alternate binary composes its own tree without an
import cycle). Migration ordering is **not** modelled in Go — the CLI only
invokes the Python runner, which owns `DB_TARGETS` and its upgrade/rollback
order.

## Breaking change: unknown config keys now fail loudly

`AppConfig` sets `model_config = ConfigDict(extra="forbid")`. An **unrecognized
top-level config key** — one that is neither a core field nor a *registered*
extension section — now causes a **loud failure at startup** instead of being
silently ignored. This is defensively correct (it ensures extensions are
formally registered via `register_config`), but downstream configs with
legacy/typo top-level keys must be cleaned up or migrated to a registered
extension section before upgrading.
