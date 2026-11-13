"""Integration tests for the direct-binding injection boundary (theme-5 Q-02).

Exercises ``CredentialResolver.resolve`` / ``CredentialService.select`` /
``CredentialService.inject`` with ``allowed_credential_ids`` and the
``Jentic-Credential-Id`` tie-breaker against real control-DB rows. The
headline invariant: **an unbound-but-covering credential is never selected or
injected** when the binding boundary is active.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.broker.core.errors import (
    ActionDeniedError,
    AmbiguousCredentialMatchError,
    CredentialNotProvisionedError,
    CredentialPendingError,
    InvalidCredentialNameError,
)
from jentic_one.broker.repos import AgentRuleEvaluator, CredentialBindingResolver
from jentic_one.broker.services.credentials.errors import (
    CredentialIdNotFoundError,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.broker.services.execution.authorization import (
    ExecutionAuthorization,
    authorize_execution,
)
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_VENDOR = "stripe"
_API_NAME = "payments"
_API_VERSION = "v1"
_API = APIReference(vendor=_VENDOR, name=_API_NAME, version=_API_VERSION)

_IDENTITY = Identity(
    sub="agent_42",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


@pytest.fixture()
async def clean_credentials(
    admin_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(delete(Agent))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(delete(AgentPermissionRule))
            await session.execute(delete(CustomerAPIKey))
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api_key(
    ctx: Context,
    *,
    cred_id: str,
    name: str,
    secret: str,
    state: str = "connected",
    server_variables: dict[str, str] | None = None,
) -> None:
    encrypted = ctx.encryption.encrypt(secret)
    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name=name,
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
                state=state,
                server_variables=server_variables,
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{cred_id}",
                credential_id=cred_id,
                encrypted_key=encrypted,
                location="header",
                field_name="X-Api-Key",
            )
        )
        await session.commit()


async def _seed_bound_agent(ctx: Context) -> str:
    agent = Agent(name="selection-agent", registered_by="usr_test", status="active")
    async with ctx.admin_db.session() as session:
        session.add(agent)
        await session.commit()
        return agent.id


async def _bind_with_rule(
    ctx: Context, *, agent_id: str, credential_id: str, effect: str | None
) -> None:
    async with ctx.admin_db.session() as session:
        session.add(AgentCredentialBinding(agent_id=agent_id, credential_id=credential_id))
        await session.commit()
    if effect is not None:
        async with ctx.control_db.session() as session:
            session.add(
                AgentPermissionRule(
                    agent_id=agent_id,
                    credential_id=credential_id,
                    effect=effect,
                    methods=["GET"],
                    path=".*",
                    sequence=1,
                )
            )
            await session.commit()


async def _authorize(
    ctx: Context,
    agent_id: str,
    *,
    credential_id: str | None = None,
    request_server_variables: dict[str, str] | None = None,
) -> ExecutionAuthorization:
    return await authorize_execution(
        ctx=ctx,
        identity=Identity(
            sub=agent_id,
            actor_type=ActorType.AGENT,
            permissions=["execute"],
            active=True,
        ),
        api=_API,
        operation_id="listPets",
        method="GET",
        path="/pets",
        instance="/execute",
        credential_deriver=CredentialBindingResolver(ctx.admin_db, ctx.control_db),
        agent_rule_evaluator=AgentRuleEvaluator(ctx.control_db),
        credential_id=credential_id,
        request_server_variables=request_server_variables,
    )


async def test_unbound_but_covering_credential_never_injected(
    integration_context: Context, clean_credentials: None
) -> None:
    """Q-02: a covering credential outside the allowed set must not resolve.

    ``cred_other`` covers the API and would resolve on the legacy unfiltered
    path — but the caller's binding set only contains ``cred_bound``, so the
    boundary must never let ``cred_other`` through, and an empty allowed set
    must deny everything (not act as a wildcard).
    """
    await _seed_api_key(
        integration_context,
        cred_id="cred_bound",
        name="bound",
        secret="sk-bound",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_other",
        name="other",
        secret="sk-other",  # pragma: allowlist secret
    )

    service = CredentialService(integration_context)

    # Only the bound credential resolves — never the covering sibling.
    injected = await service.inject(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        allowed_credential_ids=["cred_bound"],
    )
    assert injected.credential_id == "cred_bound"
    assert injected.headers == {"X-Api-Key": "sk-bound"}

    # An empty allowed set is a real deny-all filter, not a wildcard.
    with pytest.raises(CredentialNotProvisionedError):
        await service.inject(
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            identity=_IDENTITY,
            allowed_credential_ids=[],
        )


async def test_two_bound_credentials_ambiguity_names_binding_type(
    integration_context: Context, clean_credentials: None
) -> None:
    """Same-specificity tie within the allowed set → ambiguous_credential_binding 409."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="account-b",
        secret="sk-b",  # pragma: allowlist secret
    )

    with pytest.raises(AmbiguousCredentialMatchError) as exc:
        await CredentialService(integration_context).select(
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            identity=_IDENTITY,
            allowed_credential_ids=["cred_a", "cred_b"],
        )
    assert exc.value.type == "ambiguous_credential_binding"
    candidate_ids = {c["id"] for c in exc.value.extra["candidates"]}
    assert candidate_ids == {"cred_a", "cred_b"}
    assert exc.value.directive is not None
    assert exc.value.directive.parameters["headers"] == {"Jentic-Credential-Id": "cred_a"}


async def test_credential_id_header_breaks_tie(
    integration_context: Context, clean_credentials: None
) -> None:
    """Jentic-Credential-Id is the authoritative tie-breaker within the allowed set."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="account-b",
        secret="sk-b",  # pragma: allowlist secret
    )

    selected = await CredentialService(integration_context).select(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        credential_id="cred_b",
        allowed_credential_ids=["cred_a", "cred_b"],
    )
    assert selected is not None
    assert selected.credential_id == "cred_b"


async def test_credential_name_header_breaks_tie(
    integration_context: Context, clean_credentials: None
) -> None:
    """Jentic-Credential-Name still disambiguates on the direct path."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="account-b",
        secret="sk-b",  # pragma: allowlist secret
    )

    selected = await CredentialService(integration_context).select(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        credential_name="account-a",
        allowed_credential_ids=["cred_a", "cred_b"],
    )
    assert selected is not None
    assert selected.credential_id == "cred_a"


async def test_unknown_credential_id_maps_to_domain_error_with_candidates(
    integration_context: Context, clean_credentials: None
) -> None:
    """An id outside the covering candidates → credential_id_not_found + candidates."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )

    with pytest.raises(InvalidCredentialNameError) as exc:
        await CredentialService(integration_context).select(
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            identity=_IDENTITY,
            credential_id="cred_missing",
            allowed_credential_ids=["cred_a"],
        )
    assert exc.value.type == "credential_id_not_found"
    assert [c["id"] for c in exc.value.extra["candidates"]] == ["cred_a"]


async def test_resolver_raw_id_filter(
    integration_context: Context, clean_credentials: None
) -> None:
    """Resolver-level: the id filter is exact and raises with candidates on a miss."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    resolver = CredentialResolver(integration_context)

    resolved = await resolver.resolve(api=_API, caller="agent_42", credential_id="cred_a")
    assert resolved.credential_id == "cred_a"

    with pytest.raises(CredentialIdNotFoundError):
        await resolver.resolve(api=_API, caller="agent_42", credential_id="cred_nope")


async def test_inject_with_preresolved_skips_reresolution(
    integration_context: Context, clean_credentials: None
) -> None:
    """``preresolved`` injects the selected credential without a second resolve."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="account-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    service = CredentialService(integration_context)
    selected = await service.select(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        allowed_credential_ids=["cred_a"],
    )
    assert selected is not None

    injected = await service.inject(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=_IDENTITY,
        preresolved=selected,
    )
    assert injected.credential_id == "cred_a"
    assert injected.headers == {"X-Api-Key": "sk-a"}


async def test_authorization_skips_deny_only_equal_binding(
    integration_context: Context, clean_credentials: None
) -> None:
    """A binding that cannot serve the operation does not block its usable sibling."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="denied",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="usable",
        secret="sk-b",  # pragma: allowlist secret
    )
    agent_id = await _seed_bound_agent(integration_context)
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_a", effect=None
    )
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_b", effect="allow"
    )

    result = await _authorize(integration_context, agent_id)

    assert result.selected_credential.credential_id == "cred_b"
    assert result.verdict == RuleVerdict.ALLOW


async def test_authorization_all_denied_still_denies_deterministically(
    integration_context: Context, clean_credentials: None
) -> None:
    """Removing deny-only candidates from ambiguity never turns a deny into a fallback allow."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="denied-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="denied-b",
        secret="sk-b",  # pragma: allowlist secret
    )
    agent_id = await _seed_bound_agent(integration_context)
    for credential_id in ("cred_a", "cred_b"):
        await _bind_with_rule(
            integration_context, agent_id=agent_id, credential_id=credential_id, effect="deny"
        )

    with pytest.raises(ActionDeniedError) as exc:
        await _authorize(integration_context, agent_id)

    assert exc.value.type == "action_denied"
    assert exc.value.directive is not None
    assert exc.value.directive.parameters["edit"]["credential_id"] == "cred_a"


async def test_authorization_genuine_usable_ambiguity_is_preserved(
    integration_context: Context, clean_credentials: None
) -> None:
    """Two usable equal matches remain a deterministic, actionable binding ambiguity."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="usable-a",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="usable-b",
        secret="sk-b",  # pragma: allowlist secret
    )
    agent_id = await _seed_bound_agent(integration_context)
    for credential_id in ("cred_a", "cred_b"):
        await _bind_with_rule(
            integration_context, agent_id=agent_id, credential_id=credential_id, effect="allow"
        )

    with pytest.raises(AmbiguousCredentialMatchError) as exc:
        await _authorize(integration_context, agent_id)

    assert [c["id"] for c in exc.value.extra["candidates"]] == ["cred_a", "cred_b"]
    assert exc.value.directive is not None
    assert exc.value.directive.parameters["candidates"] == ["cred_a", "cred_b"]
    assert exc.value.directive.parameters["headers"] == {"Jentic-Credential-Id": "cred_a"}


async def test_explicit_denied_credential_never_falls_back_to_allowed_sibling(
    integration_context: Context, clean_credentials: None
) -> None:
    """An explicit ID remains authoritative and cannot be bypassed by sibling fallback."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="usable",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="denied",
        secret="sk-b",  # pragma: allowlist secret
    )
    agent_id = await _seed_bound_agent(integration_context)
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_a", effect="allow"
    )
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_b", effect="deny"
    )

    with pytest.raises(ActionDeniedError) as exc:
        await _authorize(integration_context, agent_id, credential_id="cred_b")

    assert exc.value.directive is not None
    assert exc.value.directive.parameters["edit"]["credential_id"] == "cred_b"


async def test_authorization_approval_candidate_skips_denied_sibling(
    integration_context: Context, clean_credentials: None
) -> None:
    """A sole require-approval candidate enters the hold path without a denied sibling tie."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="denied",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="held",
        secret="sk-b",  # pragma: allowlist secret
    )
    agent_id = await _seed_bound_agent(integration_context)
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_a", effect="deny"
    )
    await _bind_with_rule(
        integration_context,
        agent_id=agent_id,
        credential_id="cred_b",
        effect="require-approval",
    )

    result = await _authorize(integration_context, agent_id)

    assert result.selected_credential.credential_id == "cred_b"
    assert result.verdict == RuleVerdict.REQUIRE_APPROVAL
    assert result.matched_rule_id is not None


async def test_pending_binding_returns_connect_directive(
    integration_context: Context, clean_credentials: None
) -> None:
    """A pending binding reports its lifecycle instead of an identity mismatch."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_pending",
        name="pending",
        secret="sk-pending",  # pragma: allowlist secret
        state="pending",
    )
    agent_id = await _seed_bound_agent(integration_context)
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_pending", effect="allow"
    )

    with pytest.raises(CredentialPendingError) as exc:
        await _authorize(integration_context, agent_id)

    assert exc.value.type == "credential_pending"
    assert exc.value.directive is not None
    assert exc.value.directive.strategy == "prompt_human"
    assert exc.value.directive.parameters["state"] == "pending"
    assert "complete" in exc.value.directive.human_readable_instruction


async def test_server_variable_inapplicable_binding_does_not_interfere(
    integration_context: Context, clean_credentials: None
) -> None:
    """Policy filtering only sees candidates already compatible with the request URL."""
    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="eu",
        secret="sk-a",  # pragma: allowlist secret
        server_variables={"region": "eu"},
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="us",
        secret="sk-b",  # pragma: allowlist secret
        server_variables={"region": "us"},
    )
    agent_id = await _seed_bound_agent(integration_context)
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_a", effect="deny"
    )
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_b", effect="allow"
    )

    result = await _authorize(
        integration_context, agent_id, request_server_variables={"region": "us"}
    )

    assert result.selected_credential.credential_id == "cred_b"


async def test_cold_tied_selection_does_not_deadlock_a_constrained_pool(
    integration_context: Context,
    integration_config: AppConfig,
    clean_credentials: None,
) -> None:
    """Policy reads never wait for a connection held by credential selection."""
    if integration_config.databases.control.backend != "postgres":
        pytest.skip("PostgreSQL connection-pool regression")

    await _seed_api_key(
        integration_context,
        cred_id="cred_a",
        name="denied",
        secret="sk-a",  # pragma: allowlist secret
    )
    await _seed_api_key(
        integration_context,
        cred_id="cred_b",
        name="usable",
        secret="sk-b",  # pragma: allowlist secret
    )
    agent_id = await _seed_bound_agent(integration_context)
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_a", effect="deny"
    )
    await _bind_with_rule(
        integration_context, agent_id=agent_id, credential_id="cred_b", effect="allow"
    )

    config = integration_config.model_copy(deep=True)
    config.databases.control.pool_max = 1
    concurrency = 11
    ready = 0
    ready_lock = asyncio.Lock()
    release_policy_reads = asyncio.Event()

    async def policy_filter(candidate_ids: list[str]) -> list[str]:
        nonlocal ready
        evaluator = AgentRuleEvaluator(constrained.control_db)
        async with ready_lock:
            ready += 1
            if ready == concurrency:
                release_policy_reads.set()
        # Every resolver has reached its policy hook before any hook starts a
        # nested rule read. A fresh evaluator makes each rule lookup cold.
        async with asyncio.timeout(2):
            await release_policy_reads.wait()
        viable: list[str] = []
        for candidate_id in candidate_ids:
            evaluation = await evaluator.evaluate(
                agent_id=agent_id,
                credential_id=candidate_id,
                rule_set_id=None,
                method="GET",
                path="/pets",
                operation_id="listPets",
            )
            if evaluation.verdict != RuleVerdict.DENY:
                viable.append(candidate_id)
        return viable

    async with Context(config) as constrained:
        # SQLAlchemy adds ten overflow connections by default. Eleven cold,
        # concurrent requests therefore saturate this real pool if every outer
        # credential read retains its connection while its policy read waits.
        constrained.control_db.engine.pool._timeout = 0.1  # type: ignore[attr-defined]
        tasks: list[asyncio.Task[ResolvedCredential]] = []
        async with asyncio.TaskGroup() as group:
            for _ in range(concurrency):
                resolver = CredentialResolver(constrained)
                tasks.append(
                    group.create_task(
                        resolver.resolve(
                            api=_API,
                            caller=agent_id,
                            allowed_credential_ids=["cred_a", "cred_b"],
                            candidate_filter=policy_filter,
                        )
                    )
                )

    assert all(task.result().credential_id == "cred_b" for task in tasks)
