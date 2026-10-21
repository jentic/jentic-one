"""Integration tests for in-process broker cache invalidation (#1837).

Verifies that when ``install_broker_auth`` is wired onto a shared ``Context``,
mutating paths in ``AgentService``, ``AgentAuthService``, ``TokenService`` and
``CredentialService`` immediately evict the affected entries in:
- ``CachingCredentialDeriver``
- ``AgentRuleEvaluator``
- ``CachedTokenValidator`` (opaque tokens + API keys)
- ``CachedActorStatus`` (JWT path)
with zero wait for the 30 s cache TTL.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.schemas.agents import AgentCreatePayload
from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.services.credentials.schemas.credentials import (
    CredentialCreate,
    CredentialUpdate,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION, ORG_ADMIN
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType

pytestmark = pytest.mark.integration

_OWNER = Identity(
    sub="usr_cache_inv_owner",
    email="owner@cache-inv.test",
    permissions=[ORG_ADMIN],
)


@pytest.fixture()
async def clean_state(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(delete(AgentCredential))
            await session.execute(delete(AccessToken))
            await session.execute(delete(ActorPermissionGrant))
            await session.execute(delete(Agent).where(Agent.owner_id == _OWNER.sub))
            await session.execute(delete(User).where(User.id == _OWNER.sub))
            await session.commit()
        async with integration_context.control_db.session() as session:
            await session.execute(delete(AgentPermissionRule))
            await session.execute(delete(TokenValueCredential))
            await session.execute(delete(Credential))
            await session.commit()

    await _clean()
    async with integration_context.admin_db.session() as session:
        session.add(
            User(
                id=_OWNER.sub,
                email=_OWNER.email,
                first_name="Cache",
                last_name="Owner",
                active=True,
                created_by=_OWNER.sub,
            )
        )
        await session.commit()
    yield
    await _clean()


async def test_unbind_disable_revoke_and_credential_delete_immediately_invalidate_broker_caches(
    integration_context: Context, clean_state: None
) -> None:
    """In a single process, unbind, rule edit, credential deactivate/delete, API-key
    revoke, and agent disable take effect immediately without waiting for the TTL (#1837)."""
    app = FastAPI()
    install_broker_auth(app, integration_context)

    token_validator = app.state.broker_token_validator
    deriver = app.state.broker_credential_deriver
    rule_evaluator = app.state.broker_agent_rule_evaluator

    agent_svc = AgentService(integration_context)
    auth_svc = AgentAuthService(integration_context)
    cred_svc = CredentialService(integration_context)

    agent = await agent_svc.create(
        AgentCreatePayload(
            name="cache-inv-agent",
            permissions=[BROKER_EXECUTE_PERMISSION],
        ),
        owner_id=_OWNER.sub,
        identity=_OWNER,
    )
    api_key = await auth_svc.register_api_key(agent.id, identity=_OWNER)

    # 1. Warm the API-key token cache; then revoke the API key -> immediate 401.
    resolved = await token_validator.validate(api_key)
    assert resolved.sub == agent.id

    await auth_svc.revoke_api_key(agent.id, identity=_OWNER)
    with pytest.raises(TokenValidationError):
        await token_validator.validate(api_key)

    # Re-issue a new API key, warm cache, then disable the agent -> immediate 401.
    api_key_2 = await auth_svc.register_api_key(agent.id, identity=_OWNER)
    assert (await token_validator.validate(api_key_2)).sub == agent.id

    await agent_svc.disable(agent.id, identity=_OWNER)
    with pytest.raises(TokenValidationError):
        await token_validator.validate(api_key_2)

    # Re-enable the agent -> immediate recovery (negative cache entry evicted).
    await agent_svc.enable(agent.id, identity=_OWNER)
    assert (await token_validator.validate(api_key_2)).sub == agent.id

    # 2. Create a credential, bind it, and warm the binding deriver + rule evaluator caches.
    cred = await cred_svc.create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="CacheInvCred",
            api=APIReference(vendor="acme-com", name="widgets", version="v1"),
            token="sk-cache-inv-secret-12345",
        ),
        identity=_OWNER,
    )
    await agent_svc.bind_credential(agent.id, credential_id=cred.credential_id, identity=_OWNER)
    await cred_svc.replace_agent_permissions(
        cred.credential_id,
        agent.id,
        [{"effect": "allow", "methods": ["GET"], "path": "/items", "match_mode": "exact"}],
        identity=_OWNER,
    )

    derived = await deriver.derive_credentials(
        agent_id=agent.id, vendor="acme-com", name="widgets", version="v1"
    )
    assert len(derived.credentials) == 1
    assert derived.credentials[0].credential_id == cred.credential_id

    eval_ok = await rule_evaluator.evaluate(
        agent_id=agent.id,
        credential_id=cred.credential_id,
        rule_set_id=None,
        method="GET",
        path="/items",
        operation_id=None,
    )
    assert eval_ok.verdict == RuleVerdict.ALLOW

    # Replace rules with deny -> rule cache invalidated immediately.
    await cred_svc.replace_agent_permissions(
        cred.credential_id,
        agent.id,
        [{"effect": "deny", "methods": ["GET"], "path": "/items", "match_mode": "exact"}],
        identity=_OWNER,
    )
    eval_denied = await rule_evaluator.evaluate(
        agent_id=agent.id,
        credential_id=cred.credential_id,
        rule_set_id=None,
        method="GET",
        path="/items",
        operation_id=None,
    )
    assert eval_denied.verdict == RuleVerdict.DENY

    # 3. Suspend (unbind) the binding -> deriver cache invalidated immediately.
    await agent_svc.unbind_credential(
        agent.id, credential_id=cred.credential_id, purge=False, identity=_OWNER
    )
    derived_after_unbind = await deriver.derive_credentials(
        agent_id=agent.id, vendor="acme-com", name="widgets", version="v1"
    )
    assert derived_after_unbind.credentials == ()

    # Resume binding -> deriver cache invalidated immediately.
    await agent_svc.resume_credential(agent.id, credential_id=cred.credential_id, identity=_OWNER)
    derived_after_resume = await deriver.derive_credentials(
        agent_id=agent.id, vendor="acme-com", name="widgets", version="v1"
    )
    assert len(derived_after_resume.credentials) == 1

    # 4. Deactivate credential -> deriver cache invalidated immediately.
    await cred_svc.update(
        cred.credential_id,
        CredentialUpdate(type=CredentialType.BEARER_TOKEN, active=False),
        identity=_OWNER,
    )
    derived_after_deactivate = await deriver.derive_credentials(
        agent_id=agent.id, vendor="acme-com", name="widgets", version="v1"
    )
    assert derived_after_deactivate.credentials == ()

    # Re-activate credential, warm cache, then delete credential -> invalidated immediately.
    await cred_svc.update(
        cred.credential_id,
        CredentialUpdate(type=CredentialType.BEARER_TOKEN, active=True),
        identity=_OWNER,
    )
    assert (
        len(
            (
                await deriver.derive_credentials(
                    agent_id=agent.id, vendor="acme-com", name="widgets", version="v1"
                )
            ).credentials
        )
        == 1
    )
    await cred_svc.delete(cred.credential_id, identity=_OWNER)
    derived_after_delete = await deriver.derive_credentials(
        agent_id=agent.id, vendor="acme-com", name="widgets", version="v1"
    )
    assert derived_after_delete.credentials == ()
