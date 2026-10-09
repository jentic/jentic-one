"""Integration tests for the broker's open-connect-session read and ``provisioning_url``.

Seeds real control-DB connect sessions (each with its upfront ``pending``
credential) and asserts:

- ``OpenConnectSessionReader`` finds the agent's newest live session whose
  credential covers the API, and ignores terminal sessions, other agents and
  credentials for other APIs;
- the 424 ``credential_not_provisioned`` and the 403 ``no_credential_binding``
  directives carry that session's token-less owner deep link as
  ``provisioning_url`` (and no ``suggested_command``), and omit the field when
  the agent has no open session.

Rows get explicit ids: SQLite has no KSUID server default.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.broker.core.exceptions import ActionDeniedError, CredentialNotProvisionedError
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.broker.repos.open_connect_session import OpenConnectSessionReader
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.execution.authorization import derive_credential_bindings
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import resolved_auth_base_url
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_AGENT = "agnt_open_session_1"
_OTHER_AGENT = "agnt_open_session_2"
# A connect-minted credential carries the vendor axes only (version NULL).
_VENDOR = "github-com"
_API_NAME = "github-com-api-github-com"
_API = APIReference(vendor=_VENDOR, name=_API_NAME, version="2022-11-28")
_IDENTITY = Identity(
    sub=_AGENT,
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


@pytest.fixture()
async def clean_sessions(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ConnectSession))
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_session(
    control_db: DatabaseSession,
    *,
    session_id: str,
    agent_id: str | None = _AGENT,
    state: str = "created",
    api_vendor: str = _VENDOR,
    api_name: str | None = _API_NAME,
    created_at: datetime | None = None,
) -> None:
    credential_id = f"cred_{session_id}"
    async with control_db.session() as session:
        session.add(
            Credential(
                id=credential_id,
                type="oauth2_device_authorization",
                name="GitHub",
                api_vendor=api_vendor,
                api_name=api_name,
                created_by=agent_id or "usr_1",
                state="pending",
            )
        )
        await session.flush()
        session.add(
            ConnectSession(
                id=session_id,
                credential_id=credential_id,
                vendor="github",
                agent_id=agent_id,
                initiator_actor_id=agent_id or "usr_1",
                state=state,
                resolved_flow="device_authorization",
                poll_token_hash=f"{session_id:0<64}"[:64],
                created_at=created_at or datetime.now(UTC),
            )
        )
        await session.commit()


def _approval_url(ctx: Context, session_id: str) -> str:
    base = resolved_auth_base_url(ctx.config).rstrip("/")
    return f"{base}/app/agents?approve={session_id}"


async def _find(control_db: DatabaseSession, *, agent_id: str = _AGENT) -> str | None:
    return await OpenConnectSessionReader(control_db).find_session_id(
        agent_id=agent_id, vendor=_API.vendor, name=_API.name, version=_API.version
    )


async def test_reader_finds_a_created_session(
    control_db: DatabaseSession, clean_sessions: None
) -> None:
    await _seed_session(control_db, session_id="cs_open_created")
    assert await _find(control_db) == "cs_open_created"


async def test_reader_finds_a_polling_session(
    control_db: DatabaseSession, clean_sessions: None
) -> None:
    await _seed_session(control_db, session_id="cs_open_polling", state="polling")
    assert await _find(control_db) == "cs_open_polling"


@pytest.mark.parametrize("state", ["confirmed", "connected", "expired", "failed"])
async def test_reader_ignores_sessions_that_are_not_live(
    control_db: DatabaseSession, clean_sessions: None, state: str
) -> None:
    await _seed_session(control_db, session_id=f"cs_open_{state}", state=state)
    assert await _find(control_db) is None


async def test_reader_ignores_other_agents_and_unbound_sessions(
    control_db: DatabaseSession, clean_sessions: None
) -> None:
    await _seed_session(control_db, session_id="cs_open_other", agent_id=_OTHER_AGENT)
    await _seed_session(control_db, session_id="cs_open_unbound", agent_id=None)
    assert await _find(control_db) is None
    assert await _find(control_db, agent_id=_OTHER_AGENT) == "cs_open_other"


async def test_reader_ignores_sessions_for_another_api(
    control_db: DatabaseSession, clean_sessions: None
) -> None:
    await _seed_session(
        control_db, session_id="cs_open_slack", api_vendor="slack-com", api_name="slack-com"
    )
    await _seed_session(
        control_db, session_id="cs_open_gist", api_vendor=_VENDOR, api_name="gist-github-com"
    )
    assert await _find(control_db) is None


async def test_reader_prefers_the_newest_open_session(
    control_db: DatabaseSession, clean_sessions: None
) -> None:
    now = datetime.now(UTC)
    await _seed_session(control_db, session_id="cs_open_old", created_at=now - timedelta(minutes=5))
    await _seed_session(control_db, session_id="cs_open_new", created_at=now)
    assert await _find(control_db) == "cs_open_new"


async def test_424_links_the_open_session(
    integration_context: Context, clean_sessions: None
) -> None:
    await _seed_session(integration_context.control_db, session_id="cs_open_424")

    with pytest.raises(CredentialNotProvisionedError) as exc:
        await CredentialService(integration_context).inject(
            api_vendor=_API.vendor,
            api_name=_API.name,
            api_version=_API.version,
            identity=_IDENTITY,
            allowed_credential_ids=[],
        )

    directive = exc.value.directive
    assert directive is not None
    url = _approval_url(integration_context, "cs_open_424")
    assert directive.parameters["provisioning_url"] == url
    assert "suggested_command" not in directive.parameters
    assert url in directive.human_readable_instruction


async def test_424_without_an_open_session_omits_provisioning_url(
    integration_context: Context, clean_sessions: None
) -> None:
    await _seed_session(integration_context.control_db, session_id="cs_done_424", state="failed")

    with pytest.raises(CredentialNotProvisionedError) as exc:
        await CredentialService(integration_context).inject(
            api_vendor=_API.vendor,
            api_name=_API.name,
            api_version=_API.version,
            identity=_IDENTITY,
            allowed_credential_ids=[],
        )

    assert exc.value.directive is not None
    assert "provisioning_url" not in exc.value.directive.parameters


async def test_403_links_the_open_session(
    integration_context: Context, clean_sessions: None
) -> None:
    await _seed_session(integration_context.control_db, session_id="cs_open_403")
    deriver = CredentialBindingResolver(
        integration_context.admin_db, integration_context.control_db
    )

    with pytest.raises(ActionDeniedError) as exc:
        await derive_credential_bindings(
            deriver=deriver,
            identity=_IDENTITY,
            api=_API,
            instance="/execute",
            ctx=integration_context,
        )

    assert exc.value.type == "no_credential_binding"
    directive = exc.value.directive
    assert directive is not None
    url = _approval_url(integration_context, "cs_open_403")
    assert directive.parameters["provisioning_url"] == url
    assert "suggested_command" not in directive.parameters
    assert url in directive.human_readable_instruction


async def test_403_without_an_open_session_omits_provisioning_url(
    integration_context: Context, clean_sessions: None
) -> None:
    deriver = CredentialBindingResolver(
        integration_context.admin_db, integration_context.control_db
    )

    with pytest.raises(ActionDeniedError) as exc:
        await derive_credential_bindings(
            deriver=deriver,
            identity=_IDENTITY,
            api=_API,
            instance="/execute",
            ctx=integration_context,
        )

    assert exc.value.directive is not None
    assert "provisioning_url" not in exc.value.directive.parameters
