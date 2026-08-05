"""Web tests for the admin system router: report the latest release.

Exercises the scope-gated write (`POST /admin/system/latest-release`) and
verifies persistence directly against the admin DB. (The public read,
`GET /system/version`, lives on the control/combined surface and is covered by
`tests/unit/shared/test_system_version.py`.)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

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


async def test_report_latest_release_normalizes_and_persists(
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

    # The normalized release is what lands in the singleton row.
    async with web_context.admin_db.session() as session:
        stored = await session.scalar(select(LatestRelease.version))
    assert stored == "9.9.9"


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
    async with web_context.admin_db.session() as session:
        stored = await session.scalar(select(LatestRelease.version))
        row_count = await session.scalar(select(func.count()).select_from(LatestRelease))
    assert stored == "2.0.0"
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
