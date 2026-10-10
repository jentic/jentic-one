"""Integration tests for how ``credential.accessed`` names the acting agent.

Drives the whole resolve → inject → emit wiring against real databases
(``ApiKeyResolver`` → ``Identity`` → ``CredentialService.inject`` →
``emit_credential_access``) rather than one layer: the name a reader sees in
the summary is only as good as the name the authenticating resolver carried
forward, so the assertion has to span the seam.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select, text

from jentic_one.admin.core.schema.events import Event
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.api_key_resolver import AGENT_API_KEY_PREFIX, ApiKeyResolver
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.context import Context
from jentic_one.shared.models import StoredCredentialType
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_OWNER = "usr_cred_access_naming_owner"
_AGENT = "agnt_cred_access_naming"
_AGENT_NAME = "billing-bot"
_CREDENTIAL_ID = "cred_access_naming"
_VENDOR = "stripe"
_API_NAME = "charges"
_API_VERSION = "v1"


@pytest.fixture()
async def seeded_agent_and_credential(
    integration_context: Context,
) -> AsyncGenerator[str, None]:
    """Seed a named agent holding a ``jak_`` key plus the credential it uses.

    Yields the agent's raw API key so the test can authenticate through the
    real resolver. Both databases are cleared before and after, because the
    agent/credential rows are committed.
    """
    raw_key = f"{AGENT_API_KEY_PREFIX}{secrets.token_hex(16)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    async def _cleanup() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(Event).where(Event.type == EventType.CREDENTIAL_ACCESSED))
            await session.execute(
                text("DELETE FROM actor_permission_grants WHERE actor_id = :id"), {"id": _AGENT}
            )
            await session.execute(
                text("DELETE FROM agent_credentials WHERE agent_id = :id"), {"id": _AGENT}
            )
            await session.execute(text("DELETE FROM agents WHERE id = :id"), {"id": _AGENT})
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": _OWNER})
            await session.commit()
        async with integration_context.control_db.session() as session:
            await session.execute(
                delete(CustomerAPIKey).where(CustomerAPIKey.credential_id == _CREDENTIAL_ID)
            )
            await session.execute(delete(Credential).where(Credential.id == _CREDENTIAL_ID))
            await session.commit()

    await _cleanup()

    async with integration_context.admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, 'cred-access-naming@test.local', 'Cass', 'N')"
            ),
            {"id": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by) "
                "VALUES (:id, :name, :owner, 'system:test', 'active', 'system:test')"
            ),
            {"id": _AGENT, "name": _AGENT_NAME, "owner": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by) "
                "VALUES (:id, :agent_id, :hash, 'system:test')"
            ),
            {"id": f"agc_{_AGENT}", "agent_id": _AGENT, "hash": key_hash},
        )
        await session.execute(
            text(
                "INSERT INTO actor_permission_grants "
                "(id, actor_id, actor_type, permission, created_by) "
                "VALUES (:id, :actor_id, 'agent', :permission, 'system:test')"
            ),
            {"id": f"asg_{_AGENT}", "actor_id": _AGENT, "permission": BROKER_EXECUTE_PERMISSION},
        )
        await session.commit()

    async with integration_context.control_db.session() as session:
        session.add(
            Credential(
                id=_CREDENTIAL_ID,
                type=StoredCredentialType.API_KEY,
                name="Stripe live key",
                api_vendor=_VENDOR,
                api_name=_API_NAME,
                api_version=_API_VERSION,
            )
        )
        encrypted = integration_context.encryption.encrypt(
            "sk-live-123"
        )  # pragma: allowlist secret
        session.add(
            CustomerAPIKey(
                id=f"key-{_CREDENTIAL_ID}",
                credential_id=_CREDENTIAL_ID,
                encrypted_key=encrypted,
                location="header",
                field_name="X-Api-Key",
            )
        )
        await session.commit()

    yield raw_key

    await _cleanup()


async def test_credential_access_summary_names_the_acting_agent(
    integration_context: Context, seeded_agent_and_credential: str
) -> None:
    """Pins #1543: the ``credential.accessed`` summary names the agent that used
    the credential, the way it already names the credential.

    An agent id is opaque to the human reading the activity feed, so a summary
    that falls back to one for an agent whose name the authenticating resolver
    already read is unreadable. The id stays addressable on ``actor_id``.
    """
    identity = await ApiKeyResolver(integration_context.admin_db).resolve(
        seeded_agent_and_credential
    )
    assert identity is not None, "the seeded jak_ key must authenticate as the agent"
    assert identity.sub == _AGENT

    await CredentialService(integration_context).inject(
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        identity=identity,
    )

    async with integration_context.admin_db.session() as session:
        event = (
            await session.execute(select(Event).where(Event.type == EventType.CREDENTIAL_ACCESSED))
        ).scalar_one()

    assert event.actor_id == _AGENT, "the id stays addressable on actor_id"
    assert event.summary == (
        f"Credential 'Stripe live key' accessed by '{_AGENT_NAME}' "
        f"for {_VENDOR}/{_API_NAME}/{_API_VERSION}"
    )
    assert _AGENT not in event.summary, "the summary must not show the agent as a raw id"
