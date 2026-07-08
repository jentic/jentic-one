"""AppContainer: the dependency-injection seam for the app factories.

Carries the pluggable pieces a caller may swap (Broker, extra routers/
installers). The default container is constructed from a Context; a downstream
package can build its own and pass it to the factories. Keeping this in
``shared/web`` (not a surface) mirrors ``wiring.py``'s role as the composition
seam — it imports only ``shared/*`` + fastapi, so it introduces no
surface-boundary violation (``Broker`` lives in ``shared/broker``;
``DefaultBroker`` stays in ``broker/`` and is only referenced by the composition
root, never by ``shared/``).

All fields default to the standard wiring, so passing only a Context reproduces
today's behavior; existing callers that pass just ``ctx`` are unaffected.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI

from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.context import Context

#: A mounted router spec: ``(router, prefix, tags)`` — same shape the surface
#: ``get_routers()`` functions return. ``tags`` is a ``Sequence[str]`` (not
#: ``list``) so it composes with FastAPI's invariant ``list[str | Enum]`` param
#: after a ``list(...)`` copy at the mount site.
RouterSpec = tuple[APIRouter, str, Sequence[str]]

#: An installer run against the root app after all built-in surfaces are wired.
Installer = Callable[[FastAPI, Context], None]


@dataclass
class AppContainer:
    """Injected dependencies for building an app.

    ``broker`` is the data-plane implementation (``DefaultBroker`` by default;
    ``None`` here means "let the broker surface build its own default per
    request"). ``extra_routers`` and ``extra_installers`` run against the root app
    *after* the built-in surfaces are wired, so a caller mounts its
    routers/handlers last and never shadows a built-in route.
    """

    ctx: Context
    broker: Broker | None = None
    extra_routers: Sequence[RouterSpec] = field(default_factory=tuple)
    extra_installers: Sequence[Installer] = field(default_factory=tuple)

    @classmethod
    def default(cls, ctx: Context) -> AppContainer:
        """Default container (no extra injection).

        ``broker`` stays ``None`` → the broker surface builds a ``DefaultBroker``
        per request via its ``broker_factory`` (see ``broker/services/execution``).
        """
        return cls(ctx=ctx)
