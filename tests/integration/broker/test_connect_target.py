"""Integration tests for the broker's connect-target lookup on the denial path.

Seeds ``oauth_app_registrations`` in the control DB and asserts that the
raw-SQL ``ConnectableRegistrationReader`` lists only active rows, and that a
missing-binding 403 for an API covered by one shared OAuth-app registration
(and by no config entry) carries the structured ``parameters.connect`` and
``suggested_rules`` an agent provisions from.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.broker.core.exceptions import ActionDeniedError
from jentic_one.broker.repos.connectable_registrations import ConnectableRegistrationReader
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.broker.services.credentials.connect_target import resolve_connect_target
from jentic_one.broker.services.execution.authorization import derive_credential_bindings
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.shared.access_guidance import ConnectTarget
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import ConnectableRegistration
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_GMAIL = APIReference(vendor="googleapis-com", name="googleapis-com-gmail", version="v1")


@pytest.fixture()
async def clean_registrations(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Reset ``oauth_app_registrations`` before and after (no credentials reference them)."""

    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(OAuthAppRegistration))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed(
    control_db: DatabaseSession, *, api_vendor: str, catalog_api_id: str, is_active: bool = True
) -> str:
    registration = OAuthAppRegistration(
        name=f"Org {catalog_api_id}",
        api_vendor=api_vendor,
        flow_kind="authorization_code",
        client_id="shared-client",
        catalog_api_id=catalog_api_id,
        display_name="Google",
        is_active=is_active,
    )
    async with control_db.session() as session:
        session.add(registration)
        await session.commit()
        return registration.id


async def test_reader_lists_only_active_registrations(
    control_db: DatabaseSession, clean_registrations: None
) -> None:
    active = await _seed(control_db, api_vendor="google", catalog_api_id="googleapis.com/gmail")
    await _seed(
        control_db, api_vendor="google", catalog_api_id="googleapis.com/drive", is_active=False
    )

    rows = await ConnectableRegistrationReader(control_db).list_active()

    assert rows == (
        ConnectableRegistration(
            id=active, api_vendor="google", catalog_api_id="googleapis.com/gmail"
        ),
    )


async def test_resolve_connect_target_pins_the_single_covering_registration(
    integration_context: Context, clean_registrations: None
) -> None:
    gmail = await _seed(
        integration_context.control_db, api_vendor="google", catalog_api_id="googleapis.com/gmail"
    )
    await _seed(
        integration_context.control_db, api_vendor="google", catalog_api_id="googleapis.com/drive"
    )

    target = await resolve_connect_target(integration_context, _GMAIL)

    assert target == ConnectTarget(vendor_key="google", registration_id=gmail)


async def test_no_credential_binding_denial_carries_connect_from_a_registration(
    integration_context: Context, admin_db: DatabaseSession, clean_registrations: None
) -> None:
    """The real derive → deny path: an agent with no binding, an API nothing
    serves, and one shared app covering it → a 403 whose directive names the
    registration and the minimal rule for the denied request."""
    gmail = await _seed(
        integration_context.control_db, api_vendor="google", catalog_api_id="googleapis.com/gmail"
    )
    identity = Identity(sub="agnt_connect_target", actor_type=ActorType.AGENT, permissions=[])

    with pytest.raises(ActionDeniedError) as raised:
        await derive_credential_bindings(
            deriver=CredentialBindingResolver(admin_db, integration_context.control_db),
            identity=identity,
            api=_GMAIL,
            instance="/gmail/v1/users/me/messages",
            ctx=integration_context,
            method="GET",
            path="/gmail/v1/users/me/messages",
        )

    directive = raised.value.directive
    assert directive is not None
    assert raised.value.type == "no_credential_binding"
    assert directive.parameters["connect"] == {"vendor_key": "google", "registration_id": gmail}
    assert directive.parameters["suggested_command"] == "jentic connect google"
    assert directive.parameters["suggested_rules"] == [
        {
            "effect": "allow",
            "methods": ["GET"],
            "path": "/gmail/v1/users/me/messages",
            "match_mode": "exact",
        }
    ]
