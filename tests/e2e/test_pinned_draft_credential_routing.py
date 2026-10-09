"""A pinned draft revision must not carry an existing credential to new hosts.

An agent holding ``apis:write`` can import a draft revision of an API whose
``servers`` point somewhere else, and pin it on a broker call with
``Jentic-Revision``. Promoting that draft is held by the server-host change
guard (``registry/ingest/host_change_guard.py``) on a credential-bound API; the
pin path must hold it the same way, or the agent sends the bound credential to
a host nobody approved without ever promoting.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from tests.e2e.conftest import (
    E2E_VENDOR,
    Stack,
    StackAgent,
    import_inline,
    make_agent,
    recorded_requests,
    spec,
)
from tests.smoke.conftest import authed_request, broker_call

pytestmark = pytest.mark.e2e

_CANARY = "canary-" + uuid.uuid4().hex  # pragma: allowlist secret


@dataclass(frozen=True)
class BoundApi:
    agent: StackAgent
    vendor: str
    name: str
    version: str

    def pin(self, revision_id: str) -> str:
        return f"{self.vendor}:{self.name}:{self.version}=rev_{uuid.UUID(revision_id).hex}"


@pytest.fixture(scope="module")
def bound_api(stack: Stack, admin_token: str) -> BoundApi:
    """A live API on the upstream host with an API key bound to an ``apis:write`` agent.

    Module-scoped: a host serves one vendor's live API, so the tests share it.
    """
    agent = make_agent(stack, admin_token, extra_permissions=["apis:write"])
    # One vendor for every run: a live host serves a single vendor's APIs, so a
    # fresh API name (not vendor) keeps reruns against one stack independent.
    vendor = E2E_VENDOR
    name, version = f"widgets-{uuid.uuid4().hex[:8]}", "1.0.0"
    revision = import_inline(
        stack,
        admin_token,
        vendor=vendor,
        name=name,
        version=version,
        content=spec(title="Widgets", version=version, server=stack.upstream_url, path="/widgets"),
    )
    _, status = authed_request(
        f"{stack.app_url}/apis/{vendor}/{name}/{version}/revisions/{revision['revision_id']}:promote",
        method="POST",
        token=admin_token,
    )
    assert status == 200
    cred, status = authed_request(
        f"{stack.app_url}/credentials",
        method="POST",
        token=admin_token,
        body={
            "type": "api_key",
            "name": f"widgets-{uuid.uuid4().hex[:6]}",
            "api": {"vendor": vendor, "name": name, "version": version},
            "provider": "static",
            "key": _CANARY,
            "location": "header",
            "field_name": "X-Api-Key",
        },
    )
    assert status == 201 and isinstance(cred, dict), cred
    credential_id = cred["credential"]["credential_id"]
    _, status = authed_request(
        f"{stack.app_url}/agents/{agent.agent_id}/credentials",
        method="POST",
        token=admin_token,
        body={"credential_id": credential_id},
    )
    assert status == 201
    _, status = authed_request(
        f"{stack.app_url}/credentials/{credential_id}/agents/{agent.agent_id}/permissions",
        method="PUT",
        token=admin_token,
        body=[{"effect": "allow", "methods": ["GET"], "path": ".*", "match_mode": "regex"}],
    )
    assert status == 200
    return BoundApi(agent=agent, vendor=vendor, name=name, version=version)


def test_pinned_draft_cannot_route_a_bound_credential_to_a_new_host(
    recorder: Stack, bound_api: BoundApi
) -> None:
    stack = recorder
    draft = import_inline(
        stack,
        bound_api.agent.token,
        vendor=bound_api.vendor,
        name=bound_api.name,
        version=bound_api.version,
        content=spec(
            title="Widgets", version=bound_api.version, server=stack.recorder_url, path="/elsewhere"
        ),
    )
    assert draft["state"] == "draft"

    body, status, _ = broker_call(
        stack.broker_url,
        f"{stack.recorder_url}/elsewhere",
        token=bound_api.agent.token,
        headers={"Jentic-Revision": bound_api.pin(draft["revision_id"])},
    )

    leaked = [r for r in recorded_requests(stack) if _CANARY in str(r)]
    assert leaked == [], f"bound credential reached the draft's host (broker {status})"
    assert status == 403, body
    assert b"host_change_requires_operator" in body


def test_pinned_draft_on_unchanged_hosts_still_routes(recorder: Stack, bound_api: BoundApi) -> None:
    """The guard is about hosts: a draft on the live hosts still routes when pinned."""
    stack = recorder
    draft = import_inline(
        stack,
        bound_api.agent.token,
        vendor=bound_api.vendor,
        name=bound_api.name,
        version=bound_api.version,
        content=spec(
            title="Widgets", version=bound_api.version, server=stack.upstream_url, path="/v2-only"
        ),
    )
    body, _, _ = broker_call(
        stack.broker_url,
        f"{stack.upstream_url}/v2-only",
        token=bound_api.agent.token,
        headers={"Jentic-Revision": bound_api.pin(draft["revision_id"])},
    )
    assert b"host_change_requires_operator" not in body, body
    assert b"operation_not_found" not in body, body
