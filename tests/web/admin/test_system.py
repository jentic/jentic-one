"""Web tests for the system router: report + read the latest release.

Exercises the scope-gated write (`POST /admin/system/latest-release`) and its
effect on the public read (`GET /system/version`) against a real admin DB.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from jentic_one import __version__
from jentic_one.admin.core.schema.latest_releases import LatestRelease
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clean_latest_releases(web_context: Context):
    """Keep the singleton table empty between tests (order-independent)."""
    yield
    async with web_context.admin_db.session() as session:
        await session.execute(delete(LatestRelease))
        await session.commit()


def _token(web_context: Context, scopes: list[str]) -> str:
    config = web_context.config.admin.auth
    claims = {
        "sub": "usr_reporter",
        "email": "reporter@test.local",
        "actor_type": "user",
        "scopes": scopes,
        "must_change_password": False,
    }
    return issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)


def test_report_latest_release_requires_scope(
    unauthed_client: TestClient, web_context: Context
) -> None:
    """A token without instance:write (or org:admin) is rejected."""
    token = _token(web_context, ["users:read"])
    resp = unauthed_client.post(
        "/admin/system/latest-release",
        json={"version": "v0.26.0"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_report_latest_release_unauthenticated(unauthed_client: TestClient) -> None:
    resp = unauthed_client.post("/admin/system/latest-release", json={"version": "v0.26.0"})
    assert resp.status_code == 401


def test_report_latest_release_normalizes_and_persists(
    unauthed_client: TestClient, web_context: Context
) -> None:
    """instance:write can report; the stored value drops the leading v."""
    token = _token(web_context, ["instance:write"])
    resp = unauthed_client.post(
        "/admin/system/latest-release",
        json={"version": "v9.9.9"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == "9.9.9"

    # Public read now reflects the reported release and flags an update (9.9.9 is
    # far ahead of the running __version__).
    read = unauthed_client.get("/system/version").json()
    assert read["current"] == __version__
    assert read["latest"] == "9.9.9"
    assert read["update_available"] is True


async def test_report_latest_release_upserts_single_row(
    unauthed_client: TestClient, web_context: Context
) -> None:
    """Reporting twice updates the same singleton row rather than inserting."""
    token = _token(web_context, ["instance:write"])
    headers = {"Authorization": f"Bearer {token}"}
    unauthed_client.post(
        "/admin/system/latest-release", json={"version": "1.0.0"}, headers=headers
    )
    unauthed_client.post(
        "/admin/system/latest-release", json={"version": "2.0.0"}, headers=headers
    )
    assert unauthed_client.get("/system/version").json()["latest"] == "2.0.0"
    async with web_context.admin_db.session() as session:
        row_count = await session.scalar(select(func.count()).select_from(LatestRelease))
    assert row_count == 1


def test_report_latest_release_rejects_bad_version(
    unauthed_client: TestClient, web_context: Context
) -> None:
    token = _token(web_context, ["instance:write"])
    resp = unauthed_client.post(
        "/admin/system/latest-release",
        json={"version": "not-a-version"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_report_latest_release_org_admin_allowed(
    authed_client: TestClient,
) -> None:
    """org:admin implies instance:write, so the default admin can report."""
    resp = authed_client.post("/admin/system/latest-release", json={"version": "3.1.4"})
    assert resp.status_code == 200
    assert resp.json()["version"] == "3.1.4"
