"""HTTP contract of the credential check (#630): ``POST /credentials/{id}:check``
and the opt-in ``check`` on ``POST /credentials``.

The service is mocked at the boundary; the probe itself is covered in
``tests/unit/test_credential_check.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.web.app import get_exception_handlers
from jentic_one.control.web.deps import get_credential_checker, get_credential_service
from jentic_one.control.web.routers import credentials as credentials_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.credential_check import CredentialCheckResult, CredentialCheckStatus
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.schemas import APIReference
from jentic_one.shared.web import deps as shared_deps

_OWNER = Identity(sub="usr_alice", permissions=["credentials:write"])
_VERDICT = CredentialCheckResult(
    status=CredentialCheckStatus.BAD_KEY,
    reason="us.posthog.com rejected the credential (HTTP 401).",
    probe="GET https://us.posthog.com/api/users/@me/",
    upstream_status=401,
)


def _app(svc: Any, checker: Any = None) -> FastAPI:
    app = FastAPI()
    app.include_router(credentials_router.router)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.dependency_overrides[get_credential_service] = lambda: svc
    app.dependency_overrides[get_credential_checker] = lambda: checker
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: _OWNER
    return app


def test_check_returns_the_named_verdict() -> None:
    svc = AsyncMock(spec=CredentialService)
    svc.check = AsyncMock(return_value=_VERDICT)
    checker = MagicMock()
    with TestClient(_app(svc, checker)) as client:
        resp = client.post("/credentials/cred_1:check")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "bad_key",
        "reason": "us.posthog.com rejected the credential (HTTP 401).",
        "probe": "GET https://us.posthog.com/api/users/@me/",
        "upstream_status": 401,
    }
    assert svc.check.await_args is not None
    assert svc.check.await_args.args == ("cred_1",)
    assert svc.check.await_args.kwargs == {"identity": _OWNER, "checker": checker}


def test_check_of_an_unknown_or_unowned_credential_is_404() -> None:
    svc = AsyncMock(spec=CredentialService)
    svc.check = AsyncMock(side_effect=CredentialNotFoundError("cred_nope"))
    with TestClient(_app(svc)) as client:
        resp = client.post("/credentials/cred_nope:check")
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("credential_not_found")


def _created() -> SimpleNamespace:
    return SimpleNamespace(
        credential_id="cred_1",
        type=CredentialType.API_KEY,
        name="posthog",
        api=APIReference(vendor="posthog-com", name="posthog", version="1"),
        catalog_api_id=None,
        provider="static",
        active=True,
        created_at=datetime(2026, 10, 6, tzinfo=UTC),
        server_variables=None,
        secret=MagicMock(model_dump=lambda: {"key": "***"}),
    )


_BODY = {
    "type": "api_key",
    "name": "posthog",
    "api": {"vendor": "posthog.com", "name": "posthog", "version": "1"},
    "key": "phx_live_s3cr3t_value",
    "location": "header",
    "field_name": "Authorization",
}


@pytest.mark.parametrize(("opt_in", "checked"), [(None, False), (False, False), (True, True)])
def test_create_checks_only_when_asked(opt_in: bool | None, checked: bool) -> None:
    svc = AsyncMock(spec=CredentialService)
    svc.create = AsyncMock(return_value=_created())
    svc.check = AsyncMock(return_value=_VERDICT)
    body = _BODY if opt_in is None else {**_BODY, "check": opt_in}
    with TestClient(_app(svc, MagicMock())) as client:
        resp = client.post("/credentials", json=body)
    assert resp.status_code == 201
    assert (resp.json()["check"] is not None) is checked
    assert svc.check.await_count == int(checked)
    if checked:
        # The save stands either way; the verdict only rides along.
        assert resp.json()["credential"]["credential_id"] == "cred_1"
        assert resp.json()["check"]["status"] == "bad_key"


# --- the service: owner-scoped, and honest when it cannot check -------------


def _service() -> CredentialService:
    ctx = MagicMock()
    session = MagicMock()
    ctx.control_db.session.return_value.__aenter__ = AsyncMock(return_value=session)
    ctx.control_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return CredentialService(ctx)


async def test_service_refuses_a_credential_the_caller_cannot_manage() -> None:
    checker = MagicMock(check=AsyncMock())
    with (
        patch(
            "jentic_one.control.services.credentials.service.CredentialRepository.get_by_id",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(CredentialNotFoundError),
    ):
        await _service().check("cred_1", identity=_OWNER, checker=checker)
    checker.check.assert_not_awaited()


async def test_service_delegates_or_says_why_it_cannot() -> None:
    checker = MagicMock(check=AsyncMock(return_value=_VERDICT))
    with patch(
        "jentic_one.control.services.credentials.service.CredentialRepository.get_by_id",
        new=AsyncMock(return_value=MagicMock()),
    ):
        assert await _service().check("cred_1", identity=_OWNER, checker=checker) is _VERDICT
        untested = await _service().check("cred_1", identity=_OWNER, checker=None)
    assert untested.status is CredentialCheckStatus.UNTESTED
    assert "registry" in untested.reason
