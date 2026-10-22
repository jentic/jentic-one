"""One connect for a CLI profile: one credential and one binding covering exactly its APIs.

``jentic connect github`` (``POST /integrations:connect`` for the GitHub
profile's own connect entry, with a preset) creates one pending credential
marked with the profile, whose rules are stored with the API role they apply
to. Once connected, the broker's matchers cover the GitHub REST, GraphQL and
git APIs the manifest declares with that credential, and no other API of the
vendor; each API is decided by its own role's rules only (default deny for a
role without rules), and the one stored grant is the one refresh rotates.

A vendor-wide credential that is not a profile credential keeps covering
every API of its vendor, with every rule of its binding evaluated.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from jentic_one.broker.repos.agent_rule_evaluator import AgentRuleEvaluator
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.broker.services.execution.authorization import binding_api_role
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.repos import OAuthTokenRepository
from jentic_one.control.services.credentials.providers.direct_oauth2 import (
    DirectOAuth2Provider,
    InvalidGrantError,
)
from jentic_one.control.services.credentials.providers.registry import ProviderRegistry
from jentic_one.control.services.integrations import device_authorization as df
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.config import (
    DirectOAuth2ProviderConfig,
    EgressConfig,
    VendorAuthConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import StoredCredentialType
from jentic_one.shared.permissions.git_rules import GitAction, GitItem
from jentic_one.shared.permissions.graphql_document import GraphQLCall
from jentic_one.shared.schemas import APIReference
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    OWNER,
    build_app,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_REST = APIReference(vendor="github-com", name="api-github-com", version="1.1.4")
_GRAPHQL = APIReference(vendor="github-com", name="github-graphql", version="1")
_GIT = APIReference(vendor="github-com", name="github-git", version="1")
#: Another API of the same registry vendor, which the GitHub profile does not declare.
_GISTS = APIReference(vendor="github-com", name="gists", version="1")

_REPO = "jentic/jentic-one-internal"
_PRESET = {"repo": _REPO, "branches": "jentic-e2e/*"}


def _entry(vendor: str) -> VendorAuthConfig:
    return VendorAuthConfig(
        vendor=vendor,
        display_name="GitHub",
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
def github(env: Context, monkeypatch: pytest.MonkeyPatch) -> Iterator[Context]:
    monkeypatch.setattr(
        env.config.vendors,
        "entries",
        {
            "github": _entry("github.com/api.github.com"),
            "github-git": _entry("github.com/github-git"),
        },
    )
    yield env


async def _connect(ctx: Context, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    async with AsyncClient(
        transport=ASGITransport(app=build_app(ctx, AGENT)), base_url="https://t"
    ) as http:
        resp = await http.post("/integrations:connect", json=body)
    return resp.status_code, resp.json()


async def _credentials(ctx: Context) -> list[Credential]:
    async with ctx.control_db.session() as session:
        return list((await session.execute(select(Credential))).scalars().all())


async def _connect_and_approve(ctx: Context) -> str:
    """One profile connect, approved with the reviewed rules; the credential's id."""
    status, body = await _connect(
        ctx, {"vendor": "github", "preset": "read_and_push", "preset_params": _PRESET}
    )
    assert status == 201, body
    row = await session_row(ctx, body["session_id"])
    assert row is not None
    begin = df.BeginResult(
        device_code="dev-code",
        user_code="ABCD-1234",
        verification_uri="https://github.com/login/device",
        verification_uri_complete=None,
        expires_in=900,
        interval=5,
    )
    with patch.object(df, "begin_device_authorization", new=AsyncMock(return_value=begin)):
        await svc(ctx).confirm(
            row.id,
            poll_token=None,
            confirmed_scopes=["repo"],
            # The review page sends the rules back without their roles.
            permission_rules=[
                {k: v for k, v in r.items() if k != "api_role"}
                for r in row.requested_permission_rules
            ],
            identity=OWNER,
        )
    # The device flow completes: the one grant lands on the one credential.
    async with ctx.control_db.transaction() as session:
        credential = await session.get(Credential, row.credential_id)
        assert credential is not None
        credential.state = "connected"
        session.add(
            OAuthToken(
                credential_id=credential.id,
                encrypted_access_token=ctx.encryption.encrypt("gho-access"),
                encrypted_refresh_token=ctx.encryption.encrypt("ghr-refresh"),
                expires_at=datetime.now(UTC) + timedelta(hours=8),
            )
        )
    return row.credential_id


async def test_one_connect_creates_one_profile_credential_with_role_tagged_rules(
    github: Context,
) -> None:
    ctx = github
    status, body = await _connect(
        ctx, {"vendor": "github", "preset": "read_and_push", "preset_params": _PRESET}
    )
    assert status == 201, body
    roles = [r["api_role"] for r in body["requested_permission_rules"]]
    assert set(roles) == {"rest", "graphql", "git"}

    (credential,) = await _credentials(ctx)
    assert credential.cli_profile == "github"
    assert (credential.api_vendor, credential.api_name, credential.api_version) == (
        "github-com",
        None,
        None,
    )
    assert credential.state == "pending"

    review = await svc(ctx).get_review_data(body["session_id"], poll_token=None, identity=OWNER)
    assert review.cli_profile == "github"
    assert [(a.role, a.api_name) for a in review.profile_apis] == [
        ("rest", "api-github-com"),
        ("graphql", "github-graphql"),
        ("git", "github-git"),
    ]


async def test_agent_written_rules_are_tagged_by_kind_or_refused(github: Context) -> None:
    ctx = github
    status, body = await _connect(
        ctx,
        {
            "vendor": "github",
            "requested_permission_rules": [
                {"effect": "allow", "methods": ["GET"], "path": "/user", "match_mode": "exact"},
                {"effect": "allow", "path": "fetch:o/r", "match_mode": "git"},
            ],
        },
    )
    assert status == 201, body
    assert [r["api_role"] for r in body["requested_permission_rules"]] == ["rest", "git"]

    status, problem = await _connect(
        ctx,
        {
            "vendor": "github",
            "requested_permission_rules": [
                {"effect": "allow", "path": "/x", "match_mode": "prefix", "api_role": "uploads"}
            ],
        },
    )
    assert status == 400, problem
    assert "connect_rule_api_role_invalid" in str(problem)


async def test_a_per_api_connect_stays_a_per_api_credential(github: Context) -> None:
    ctx = github
    status, body = await _connect(
        ctx, {"vendor": "github-git", "preset": "push_branches", "preset_params": _PRESET}
    )
    assert status == 201, body
    (credential,) = await _credentials(ctx)
    assert credential.cli_profile is None
    assert (credential.api_vendor, credential.api_name) == ("github-com", "github-git")


async def test_profile_credential_covers_exactly_the_declared_apis(github: Context) -> None:
    ctx = github
    credential_id = await _connect_and_approve(ctx)

    async with ctx.control_db.session() as session:
        stored = (
            (
                await session.execute(
                    select(AgentPermissionRule).where(
                        AgentPermissionRule.credential_id == credential_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {r.api_role for r in stored} == {"rest", "graphql", "git"}
    async with ctx.admin_db.session() as session:
        bindings = (
            await session.execute(
                text("SELECT credential_id FROM agent_credential_bindings WHERE agent_id = :a"),
                {"a": AGENT_ID},
            )
        ).all()
    assert [b[0] for b in bindings] == [credential_id]

    deriver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    for api in (_REST, _GRAPHQL, _GIT):
        derived = await deriver.derive_credentials(
            agent_id=AGENT_ID, vendor=api.vendor, name=api.name, version=api.version
        )
        assert [c.credential_id for c in derived.credentials] == [credential_id], api
    undeclared = await deriver.derive_credentials(
        agent_id=AGENT_ID, vendor=_GISTS.vendor, name=_GISTS.name, version=_GISTS.version
    )
    assert undeclared.credentials == ()
    assert undeclared.api_served is False
    assert undeclared.identity_mismatch is None

    resolver = CredentialResolver(ctx)
    for api in (_REST, _GRAPHQL, _GIT):
        resolved = await resolver.resolve(
            api=api, caller=AGENT_ID, allowed_credential_ids=[credential_id]
        )
        assert resolved.credential_id == credential_id
        assert resolved.cli_profile == "github"


async def test_each_api_is_decided_by_its_own_roles_rules(github: Context) -> None:
    ctx = github
    credential_id = await _connect_and_approve(ctx)
    resolved = await CredentialResolver(ctx).resolve(
        api=_REST, caller=AGENT_ID, allowed_credential_ids=[credential_id]
    )
    evaluator = AgentRuleEvaluator(ctx.control_db, cache_ttl_seconds=0)
    key: dict[str, Any] = {
        "agent_id": AGENT_ID,
        "credential_id": credential_id,
        "rule_set_id": None,
    }

    async def rest(method: str, path: str) -> RuleVerdict:
        result = await evaluator.evaluate(
            **key,
            method=method,
            path=path,
            operation_id=None,
            api_role=binding_api_role(resolved, _REST),
        )
        return result.verdict

    assert await rest("GET", f"/repos/{_REPO}/pulls") == RuleVerdict.ALLOW
    assert await rest("POST", f"/repos/{_REPO}/issues") == RuleVerdict.DENY
    assert await rest("DELETE", f"/repos/{_REPO}") == RuleVerdict.DENY

    async def graphql(operation_type: str, field: str) -> RuleVerdict:
        result = await evaluator.evaluate(
            **key,
            method="POST",
            path="/graphql",
            operation_id="graphql",
            graphql=GraphQLCall(operation_type=operation_type, top_level_fields=(field,)),  # type: ignore[arg-type]
            api_role=binding_api_role(resolved, _GRAPHQL),
        )
        return result.verdict

    assert await graphql("query", "repository") == RuleVerdict.ALLOW
    assert await graphql("mutation", "createPullRequest") == RuleVerdict.DENY

    async def git(item: GitItem) -> bool:
        result = await evaluator.evaluate_git(
            **key, items=[item], api_role=binding_api_role(resolved, _GIT)
        )
        return result.items[0].allowed

    owner, repo = _REPO.split("/")
    assert await git(GitItem(GitAction.FETCH, owner, repo))
    assert await git(GitItem(GitAction.PUSH, owner, repo, "refs/heads/jentic-e2e/smoke"))
    assert not await git(GitItem(GitAction.PUSH, owner, repo, "refs/heads/main"))
    assert not await git(GitItem(GitAction.FETCH, owner, "other-repo"))


async def test_a_vendor_wide_credential_is_unchanged(github: Context) -> None:
    """Regression: a non-profile credential covers the whole vendor and evaluates every rule."""
    ctx = github
    async with ctx.control_db.transaction() as session:
        credential = Credential(
            type=StoredCredentialType.STATIC_BEARER_TOKEN,
            name="GitHub PAT",
            api_vendor="github-com",
            api_name=None,
            api_version=None,
            created_by=OWNER.sub,
        )
        session.add(credential)
        await session.flush()
        credential_id = credential.id
        session.add(
            TokenValueCredential(
                credential_id=credential_id,
                encrypted_token_value=ctx.encryption.encrypt("ghp-pat"),
            )
        )
        session.add(
            AgentPermissionRule(
                agent_id=AGENT_ID,
                credential_id=credential_id,
                effect="allow",
                methods=["POST"],
                path="/graphql",
                match_mode="exact",
                is_system=False,
                sequence=0,
                created_by=OWNER.sub,
            )
        )
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id, suspended,"
                " created_by) VALUES ('acb_profile_reg', :a, :c, false, :o)"
            ),
            {"a": AGENT_ID, "c": credential_id, "o": OWNER.sub},
        )
    deriver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    for api in (_REST, _GRAPHQL, _GISTS):
        derived = await deriver.derive_credentials(
            agent_id=AGENT_ID, vendor=api.vendor, name=api.name, version=api.version
        )
        assert [c.credential_id for c in derived.credentials] == [credential_id], api

    resolved = await CredentialResolver(ctx).resolve(
        api=_GRAPHQL, caller=AGENT_ID, allowed_credential_ids=[credential_id]
    )
    assert resolved.cli_profile is None
    assert binding_api_role(resolved, _GRAPHQL) is None
    # A plain path rule still applies to every GraphQL field here.
    result = await AgentRuleEvaluator(ctx.control_db, cache_ttl_seconds=0).evaluate(
        agent_id=AGENT_ID,
        credential_id=credential_id,
        rule_set_id=None,
        method="POST",
        path="/graphql",
        operation_id="graphql",
        graphql=GraphQLCall(operation_type="mutation", top_level_fields=("addComment",)),
        api_role=None,
    )
    assert result.verdict == RuleVerdict.ALLOW


# ── refresh ──────────────────────────────────────────────────────────────────

_PROVIDER = "rotating_github"


class _RotatingGitHub(DirectOAuth2Provider):
    """GitHub-App-like token endpoint: every refresh token is single-use."""

    name = _PROVIDER

    def __init__(self) -> None:
        super().__init__(
            DirectOAuth2ProviderConfig(
                redirect_uri="https://app.example.com/cb", expiry_skew_seconds=60
            )
        )
        self.live_refresh_token = "ghr-0"
        self.sent: list[str] = []
        self._generation = 0

    async def _post_token(
        self, token_url: str, payload: dict[str, str], *, egress: EgressConfig | None
    ) -> dict[str, str]:
        sent = payload["refresh_token"]
        self.sent.append(sent)
        await asyncio.sleep(0.05)
        if sent != self.live_refresh_token:
            raise InvalidGrantError("refresh token reused")
        self._generation += 1
        self.live_refresh_token = f"ghr-{self._generation}"
        return {
            "access_token": f"gho-{self._generation}",
            "refresh_token": self.live_refresh_token,
            "expires_in": "28800",
        }


async def test_refresh_rotates_the_one_grant_every_api_uses(
    github: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = github
    vendor = _RotatingGitHub()
    monkeypatch.setattr(ctx, "_providers", ProviderRegistry({_PROVIDER: vendor}))
    async with ctx.control_db.transaction() as session:
        credential = Credential(
            type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
            name="GitHub",
            api_vendor="github-com",
            provider=_PROVIDER,
            cli_profile="github",
            created_by=OWNER.sub,
        )
        session.add(credential)
        await session.flush()
        credential_id = credential.id
        session.add(
            OAuthClientCredential(
                id=credential_id,
                token_url="https://github.com/login/oauth/access_token",
                client_id="gh-app",
                encrypted_client_secret=ctx.encryption.encrypt("gh-app-secret"),
            )
        )
        session.add(
            OAuthToken(
                credential_id=credential_id,
                encrypted_access_token=ctx.encryption.encrypt("gho-0"),
                encrypted_refresh_token=ctx.encryption.encrypt("ghr-0"),
                expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )

    resolver = CredentialResolver(ctx)
    snapshots = [
        await resolver.resolve(api=api, caller=AGENT_ID, allowed_credential_ids=[credential_id])
        for api in (_REST, _GRAPHQL, _GIT)
    ]
    assert {s.credential_id for s in snapshots} == {credential_id}
    # Calls to all three APIs at once: one refresh, one rotation, one token.
    tokens = await asyncio.gather(
        *(TokenRefresher(ctx).ensure_fresh(resolved=s, caller="t") for s in snapshots)
    )
    assert set(tokens) == {"gho-1"}
    assert vendor.sent == ["ghr-0"]
    async with ctx.control_db.session() as session:
        row = await OAuthTokenRepository.get_by_credential(session, credential_id)
    assert row is not None and row.encrypted_refresh_token is not None
    assert ctx.encryption.decrypt(row.encrypted_refresh_token) == "ghr-1"
    assert len(await _credentials(ctx)) == 1
