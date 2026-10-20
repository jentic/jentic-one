"""Unit tests for get_credential_recheck(): it tells a refusal from an outage."""

from __future__ import annotations

from typing import cast

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


def _recheck_outcome(failure: Exception | None) -> str:
    """Run the recheck inside a request whose app verifier raises ``failure`` (or accepts)."""
    seen: list[str] = []

    async def _verify(token: str, request: Request) -> Identity:
        seen.append(token)
        if failure is not None:
            raise failure
        return Identity(sub="usr_1", email="user@example.com")

    app = FastAPI()
    app.state.verify_token = _verify
    recheck_dep = Depends(get_credential_recheck)

    @app.get("/test")
    async def _endpoint(recheck: CredentialRecheck = recheck_dep) -> str:
        return (await recheck()).value

    client = TestClient(app)
    resp = cast(httpx.Response, client.get("/test", headers={"Authorization": "Bearer tok_1"}))
    assert resp.status_code == 200
    assert seen == ["tok_1"], "the recheck re-verifies the request's own credential"
    return cast(str, resp.json())


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
    assert _recheck_outcome(failure) == expected.value
