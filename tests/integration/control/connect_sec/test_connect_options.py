"""``GET /integrations/{vendor}/{name}/{version}/connect-options`` over HTTP.

What an agent reads before asking for a connection: the API's declared
schemes, how ``:connect`` would collect each, and which scopes a request may
name. An API the registry doesn't hold is ``404 unknown_api``.
"""

from __future__ import annotations

import pytest

from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT,
    NAME,
    OAUTH_SCHEME,
    VENDOR,
    VERSION,
    client,
    import_spec,
)

pytestmark = pytest.mark.integration


async def test_an_agent_reads_how_it_would_connect_an_api(env: Context) -> None:
    await import_spec(env, OAUTH_SCHEME)
    async with client(env, AGENT) as agent:
        resp = await agent.get(f"/integrations/{VENDOR}/{NAME}/{VERSION}/connect-options")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["vendor"], body["name"], body["version"]) == (VENDOR, NAME, VERSION)
        (scheme,) = body["schemes"]
        assert (scheme["name"], scheme["kind"], scheme["flow"]) == (
            "oauth",
            "oauth2",
            "awaiting_app",
        )
        assert scheme["scopes"] and all(s["requestable"] for s in scheme["scopes"])

        missing = await agent.get("/integrations/nope/nope/v0/connect-options")
        assert (missing.status_code, missing.json()["type"]) == (404, "unknown_api")
