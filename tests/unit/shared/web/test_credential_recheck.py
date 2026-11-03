"""Unit tests for get_credential_recheck().

It tells a refusal from an outage, and it re-applies the route's permission
requirements to the identity the verifier returns (#1836).
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from jentic.problem_details import Unauthorized

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.web.deps import (
    CredentialRecheck,
    CredentialRecheckResult,
    get_credential_recheck,
)

_REQUIRED = ["events:read"]


def _recheck_outcome(
    failure: Exception | None,
    *,
    permissions: list[str] | None = None,
    must_change_password: bool = False,
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Run the recheck inside a request whose app verifier raises ``failure`` (or accepts)."""
    seen: list[str] = []

    async def _verify(token: str, request: Request) -> Identity:
        seen.append(token)
        if failure is not None:
            raise failure
        return Identity(
            sub="usr_1",
            email="user@example.com",
            permissions=list(permissions or []),
            must_change_password=must_change_password,
        )

    app = FastAPI()
    app.state.verify_token = _verify
    recheck_dep = Depends(get_credential_recheck)

    @app.get("/test")
    async def _endpoint(recheck: CredentialRecheck = recheck_dep) -> dict[str, Any]:
        outcome = await recheck(required_permissions=required)
        return {
            "result": outcome.result.value,
            "permissions": outcome.identity.permissions if outcome.identity else None,
        }

    client = TestClient(app)
    resp = cast(httpx.Response, client.get("/test", headers={"Authorization": "Bearer tok_1"}))
    assert resp.status_code == 200
    assert seen == ["tok_1"], "the recheck re-verifies the request's own credential"
    return cast(dict[str, Any], resp.json())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (None, CredentialRecheckResult.VALID),
        (Unauthorized(detail="revoked"), CredentialRecheckResult.REFUSED),
        (ValueError("bad token"), CredentialRecheckResult.REFUSED),
        (DatabaseUnavailableError("admin db down"), CredentialRecheckResult.UNAVAILABLE),
    ],
    ids=["valid", "unauthorized", "unexpected-error", "db-unavailable"],
)
def test_recheck_distinguishes_refusal_from_outage(
    failure: Exception | None, expected: CredentialRecheckResult
) -> None:
    assert _recheck_outcome(failure)["result"] == expected.value


def test_valid_recheck_returns_the_fresh_identity() -> None:
    outcome = _recheck_outcome(None, permissions=["events:read"], required=_REQUIRED)
    assert outcome == {"result": "valid", "permissions": ["events:read"]}


@pytest.mark.parametrize(
    "permissions",
    [["events:read"], ["org:admin"], ["events:write"]],
    ids=["direct", "org-admin", "implied-by-write"],
)
def test_held_permission_stays_valid(permissions: list[str]) -> None:
    assert _recheck_outcome(None, permissions=permissions, required=_REQUIRED)["result"] == (
        CredentialRecheckResult.VALID.value
    )


def test_revoked_permission_is_forbidden_and_carries_no_identity() -> None:
    outcome = _recheck_outcome(None, permissions=["apis:read"], required=_REQUIRED)
    assert outcome == {"result": CredentialRecheckResult.FORBIDDEN.value, "permissions": None}


def test_password_rotation_required_is_forbidden() -> None:
    outcome = _recheck_outcome(
        None, permissions=["events:read"], must_change_password=True, required=_REQUIRED
    )
    assert outcome["result"] == CredentialRecheckResult.FORBIDDEN.value


def test_no_required_permissions_only_reverifies() -> None:
    assert _recheck_outcome(None, permissions=[])["result"] == CredentialRecheckResult.VALID.value
