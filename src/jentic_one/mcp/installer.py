"""Installer + lifespan for the ``/mcp`` mount.

Wired by the composition root (``jentic_one.wiring.build_default_container``)
onto **control-plane app shapes only** — the combined app and a standalone
control surface; never the broker (the broker stays MCP-free).
The installer runs through ``AppContainer.extra_installers`` (after all
built-in surfaces, so the challenge placeholder's removal from the auth router set
and this mount can never race for the path) and the session manager's task
group runs through ``AppContainer.extra_lifespans``.

The mount is installed **unconditionally on eligible shapes** and gated at
request time by ``server.mcp.enabled`` (the ``_McpDiscoveryRoute`` posture:
the disabled arm answers the framework's plain 404 / the discovery
challenge, so flipping the flag needs no process rebuild and the gate state
stays unobservable at build level).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.routing import Route

from jentic_one.mcp.app import McpChallengePlaceholder, McpMount
from jentic_one.shared.context import Context

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_STATE_ATTR = "mcp_mount"


def install_mcp_mount(app: FastAPI, ctx: Context) -> None:
    """Register the MCP app on the exact ``/mcp`` path of the root app.

    Deliberately a method-less exact-path ``Route`` (all methods dispatch to
    the ASGI app) rather than an ``app.mount`` prefix ``Mount``:

    - ``Mount("/mcp")`` never matches the bare ``/mcp`` — Starlette would
      307-redirect every probe to ``/mcp/``, breaking the challenge contract
      (clients POST ``/mcp`` exactly, and the challenge placeholder answered there).
    - Sub-paths (the ``/mcp/.well-known/…`` discovery probe variants) keep
      falling through to the framework's plain route-not-found 404, exactly as
      before, because no prefix route exists to swallow them.
    - The routing-level tells the pinned tests assert stay identical: the
      ``redirect_slashes`` 307 on ``/mcp/``, no ``Allow`` method tell, and the
      path stays out of the OpenAPI schema (a plain ``Route`` is invisible to
      FastAPI's schema walk).
    """
    mount = McpMount(ctx, app)
    app.state.mcp_mount = mount
    app.router.routes.append(Route("/mcp", mount, methods=None, include_in_schema=False))


def install_mcp_challenge_placeholder(app: FastAPI, ctx: Context) -> None:
    """Register the discovery challenge placeholder on ``/mcp`` (auth-sans-control).

    Wired by the composition root onto shapes that serve the auth surface
    WITHOUT control: the real mount lives where control lives, but the
    RFC 9728 discovery pointers this shape serves must not dangle into a plain
    404 — the placeholder keeps answering the discovery challenge contract. Same
    exact-path ``Route`` registration mechanics as the mount (no prefix
    ``Mount``), so the routing-level tells stay identical.
    """
    placeholder = McpChallengePlaceholder(ctx)
    app.router.routes.append(Route("/mcp", placeholder, methods=None, include_in_schema=False))


@asynccontextmanager
async def mcp_lifespan(app: FastAPI, ctx: Context) -> AsyncIterator[None]:
    """Run the stateless session manager's task group for the app's lifetime."""
    mount: McpMount | None = getattr(app.state, _STATE_ATTR, None)
    if mount is None:
        # A shape whose container carries the lifespan but not the installer
        # (never wired by build_default_container; tolerated for test apps).
        yield
        return
    async with mount.session_manager.run():
        yield
