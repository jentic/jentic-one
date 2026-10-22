"""The CLI profile routes' HTTP contract, against a stubbed provisioning service.

The listing is built from the real shipped manifests; provisioning itself
(imports, idempotence, both backends) is covered by
``tests/integration/registry/services/test_cli_profile_provisioning.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.registry.services.cli_profile_service import (
    ApiAvailability,
    ProvisionedApi,
    ProvisionResult,
)
from jentic_one.registry.services.errors import CliProfileNotFoundError, RegistryServiceError
from jentic_one.registry.web.deps import get_cli_profile_service
from jentic_one.registry.web.errors import service_error_handler
from jentic_one.registry.web.routers import cli_profiles
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.cli_profiles import CliProfile, load_profiles
from jentic_one.shared.web.deps import resolve_identity


class _StubService:
    def list_profiles(self) -> list[tuple[CliProfile, bool]]:
        return [(p, p.profile == "github") for _, p in sorted(load_profiles().items())]

    async def availability(self, profile: CliProfile) -> list[ApiAvailability]:
        return [
            ApiAvailability(a, a.role == "git", a.identity.vendor, a.identity.name, None)
            for a in profile.apis
        ]

    async def provision(self, name: str, identity: Identity) -> ProvisionResult:
        if name != "github":
            raise CliProfileNotFoundError(name, ["cloudflare", "github"])
        return ProvisionResult(
            profile="github",
            vendor="github",
            vendor_enabled=True,
            apis=(
                ProvisionedApi(
                    "rest",
                    "catalog:github.com/api.github.com",
                    "github.com/api.github.com",
                    "github-com",
                    "api-github-com",
                    None,
                    "import_queued",
                    job_id="job_1",
                ),
                ProvisionedApi(
                    "git",
                    "builtin:github.com/github-git",
                    "github.com/github-git",
                    "github-com",
                    "github-git",
                    "1",
                    "builtin",
                ),
            ),
        )


def _client(permissions: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(cli_profiles.router)
    app.add_exception_handler(RegistryServiceError, service_error_handler)
    identity = Identity(sub="usr_test", permissions=permissions)
    app.dependency_overrides[resolve_identity] = lambda: identity
    app.dependency_overrides[get_cli_profile_service] = lambda: _StubService()
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture()
def operator() -> Iterator[TestClient]:
    yield _client(["capabilities:read", "catalog:import"])


def test_list_describes_every_profile(operator: TestClient) -> None:
    resp = operator.get("/cli-profiles")
    assert resp.status_code == 200
    data = {p["profile"]: p for p in resp.json()["data"]}
    assert set(data) == {"github", "cloudflare"}
    github = data["github"]
    assert github["vendor_enabled"] is True and github["cli"] == "gh"
    assert github["credential_mode"] == "profile_scoped"
    assert github["connect"] == "github"
    assert data["cloudflare"]["connect"] is None
    assert [(a["role"], a["connect"], a["live"]) for a in github["apis"]] == [
        ("rest", None, False),
        ("graphql", "github-graphql", False),
        ("git", "github-git", True),
    ]
    presets = {p["name"]: p for p in github["presets"]}
    assert presets["push_branches"]["roles"] == ["git"]
    assert [p["name"] for p in presets["push_branches"]["params"]] == ["repo", "branches", "force"]
    assert {h["kind"] for h in github["hard_deny"]} == {
        "graphql_mutation",
        "graphql_held_mutation",
        "git_ref",
    }


def test_provision_reports_each_api(operator: TestClient) -> None:
    resp = operator.post("/cli-profiles/github:provision")
    assert resp.status_code == 200
    body = resp.json()
    assert body["vendor_enabled"] is True
    assert [(a["role"], a["status"], a["job_id"]) for a in body["apis"]] == [
        ("rest", "import_queued", "job_1"),
        ("git", "builtin", None),
    ]


def test_provision_unknown_profile_is_404(operator: TestClient) -> None:
    resp = operator.post("/cli-profiles/gitlab:provision")
    assert resp.status_code == 404
    assert "cloudflare, github" in resp.json()["detail"]


def test_provision_needs_catalog_import() -> None:
    resp = _client(["capabilities:read"]).post("/cli-profiles/github:provision")
    assert resp.status_code == 403


def test_list_needs_capabilities_read() -> None:
    assert _client([]).get("/cli-profiles").status_code == 403
