"""Connect with a CLI-profile preset: expanded server-side into the rules the approver sees.

``POST /integrations:connect`` with ``preset`` (and ``preset_params``) for a
vendor entry a CLI profile declares stores the preset's rules for that API's
role on the session, after any rules the agent listed itself, and answers
them. A connect for the GitHub GraphQL entry also provisions the API from the
spec shipped with Jentic, through the catalog auto-import seam.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from jentic_one.registry.core.schema.apis import Api
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.wiring import InProcessCatalogAutoImporter
from tests.integration.control.connect_sec.support import AGENT, build_app, session_row

pytestmark = pytest.mark.integration


def _entry(vendor: str) -> VendorAuthConfig:
    return VendorAuthConfig(
        vendor=vendor,
        display_name=vendor,
        flows=[
            VendorDeviceAuthorizationFlowConfig(
                client_id="gh-public-client",
                authorization_endpoint="https://github.com/login/device/code",
                token_endpoint="https://github.com/login/oauth/access_token",
            )
        ],
        scopes=[VendorScopeConfig(name="repo", classification="write", default=True)],
    )


@pytest.fixture()
def github_entries(env: Context, monkeypatch: pytest.MonkeyPatch) -> Iterator[Context]:
    monkeypatch.setattr(
        env.config.vendors,
        "entries",
        {
            "github": _entry("github.com/api.github.com"),
            "github-graphql": _entry("github.com/github-graphql"),
            "github-git": _entry("github.com/github-git"),
            "stripe": _entry("stripe.com/api.stripe.com"),
        },
    )
    yield env


def _client(ctx: Context) -> AsyncClient:
    app = build_app(ctx, AGENT)
    app.state.catalog_auto_importer = InProcessCatalogAutoImporter(ctx)
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://t")


def _rules(body: dict[str, object]) -> list[dict[str, object]]:
    """The answered rules without their unset (null) fields."""
    rules = body["requested_permission_rules"]
    assert isinstance(rules, list)
    return [{k: v for k, v in rule.items() if v is not None} for rule in rules]


async def _connect(ctx: Context, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    async with _client(ctx) as http:
        resp = await http.post("/integrations:connect", json=body)
    return resp.status_code, resp.json()


async def test_push_branches_preset_expands_into_git_rules(github_entries: Context) -> None:
    ctx = github_entries
    status, body = await _connect(
        ctx,
        {
            "vendor": "github-git",
            "preset": "push_branches",
            "preset_params": {
                "repo": "octo-org/app,octo-org/lib",
                "branches": "agent/*",
                "force": "true",
            },
        },
    )
    assert status == 201, body
    expected = [
        {
            "effect": "allow",
            "match_mode": "git",
            "path": f"{action}:octo-org/{repo}:refs/heads/agent/*",
        }
        for action in ("push", "force")
        for repo in ("app", "lib")
    ]
    assert _rules(body) == expected
    row = await session_row(ctx, str(body["session_id"]))
    assert row is not None
    assert row.requested_permission_rules == expected


async def test_preset_rules_follow_the_agents_own(github_entries: Context) -> None:
    ctx = github_entries
    own = {"effect": "deny", "methods": ["GET"], "path": "/user/emails", "match_mode": "exact"}
    status, body = await _connect(
        ctx,
        {
            "vendor": "github",
            "preset": "read_only",
            "preset_params": {"repo": "octo-org/app"},
            "requested_permission_rules": [own],
        },
    )
    assert status == 201, body
    # The profile's own connect: every role's rules, each tagged with its role.
    rules = _rules(body)
    assert rules[:2] == [
        {**own, "api_role": "rest"},
        {
            "effect": "allow",
            "methods": ["GET"],
            "path": "/",
            "match_mode": "prefix",
            "api_role": "rest",
        },
    ]
    assert {r["api_role"] for r in rules} == {"rest", "graphql", "git"}


async def test_graphql_connect_expands_and_provisions_the_shipped_spec(
    github_entries: Context,
) -> None:
    ctx = github_entries
    status, body = await _connect(ctx, {"vendor": "github-graphql", "preset": "read_and_prs"})
    assert status == 201, body
    rules = _rules(body)
    assert {r["match_mode"] for r in rules} == {"graphql"}
    assert rules[0] == {"effect": "allow", "path": "query", "match_mode": "graphql"}
    async with ctx.registry_db.session() as session:
        api = (
            await session.execute(
                select(Api).where(Api.catalog_api_id == "github.com/github-graphql")
            )
        ).scalar_one()
    assert (api.vendor, api.name, api.version) == ("github-com", "github-graphql", "1")
    assert api.current_revision_id is not None


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"vendor": "stripe", "preset": "read_only"}, 400, "connect_preset_not_applicable"),
        ({"vendor": "github-git", "preset": "read_only"}, 400, "connect_preset_invalid"),
        ({"vendor": "github", "preset": "push_branches"}, 400, "connect_preset_invalid"),
        ({"vendor": "github", "preset": "nope"}, 400, "connect_preset_invalid"),
        (
            {"vendor": "github-git", "preset": "read_only", "preset_params": {"repo": "x"}},
            400,
            "connect_preset_invalid",
        ),
    ],
)
async def test_unusable_presets_are_refused(
    github_entries: Context, body: dict[str, object], status: int, code: str
) -> None:
    got, problem = await _connect(github_entries, body)
    print(problem)
    assert got == status, problem
    assert problem.get("error_code", problem.get("type")) in (
        code,
        f"/errors/{code}",
    ) or code in str(problem)


async def test_preset_params_need_a_preset(github_entries: Context) -> None:
    status, _ = await _connect(
        github_entries, {"vendor": "github", "preset_params": {"repo": "o/r"}}
    )
    assert status == 422
