"""Statement budget for broker credential resolution (#1698).

``CredentialResolver.resolve`` runs on every broker call. These tests seed one
credential of every stored type in the real control DB, resolve it, and count
the SQL statements the control engine issues: the header query plus at most
one secret-material query for the candidate type(s), never a fan-out over every
child table. They also pin that each type still resolves to the same material.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, event

from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import StoredCredentialType
from jentic_one.shared.models.credentials import CredentialLocation, CredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_VENDOR = "perf-1698-vendor"
_API = APIReference(vendor=_VENDOR, name="payments", version="v1")
_EXPIRES = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.fixture()
async def clean_vendor(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Remove this module's credentials (children go via ``ON DELETE CASCADE``)."""

    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


@contextmanager
def _count_statements(db: DatabaseSession) -> Iterator[list[str]]:
    statements: list[str] = []

    def _record(*args: Any) -> None:
        statements.append(args[2])

    sync_engine = db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(sync_engine, "before_cursor_execute", _record)


def _no_child(cred_id: str) -> list[Any]:
    return []


def _bearer_child(cred_id: str) -> list[Any]:
    return [TokenValueCredential(credential_id=cred_id, encrypted_token_value="enc-bearer")]


def _api_key_child(cred_id: str) -> list[Any]:
    return [
        CustomerAPIKey(
            credential_id=cred_id,
            encrypted_key="enc-key",
            location="query",
            field_name="api_key",
        )
    ]


def _basic_child(cred_id: str) -> list[Any]:
    return [BasicCredential(credential_id=cred_id, username="alice", encrypted_password="enc-pw")]


def _oauth_child(cred_id: str) -> list[Any]:
    return [
        OAuthToken(
            credential_id=cred_id,
            encrypted_access_token="enc-access",
            encrypted_refresh_token="enc-refresh",
            expires_at=_EXPIRES,
        )
    ]


def _sigv4_child(cred_id: str) -> list[Any]:
    return [
        Sigv4Credential(
            credential_id=cred_id,
            access_key_id="AKIAEXAMPLE",
            encrypted_secret_access_key="enc-sak",  # pragma: allowlist secret
            encrypted_session_token="enc-st",
            region="eu-west-1",
            service="s3",
            created_by="usr_owner",
        )
    ]


_CASES: list[tuple[StoredCredentialType, Callable[[str], list[Any]], dict[str, Any], int]] = [
    (StoredCredentialType.NO_AUTH, _no_child, {"wire_type": CredentialType.NO_AUTH}, 1),
    (
        StoredCredentialType.STATIC_BEARER_TOKEN,
        _bearer_child,
        {
            "wire_type": CredentialType.BEARER_TOKEN,
            "encrypted_secret": "enc-bearer",  # pragma: allowlist secret
        },
        2,
    ),
    (
        StoredCredentialType.SESSION_TOKEN,
        _bearer_child,
        {
            "wire_type": CredentialType.BEARER_TOKEN,
            "encrypted_secret": "enc-bearer",  # pragma: allowlist secret
        },
        2,
    ),
    (
        StoredCredentialType.API_KEY,
        _api_key_child,
        {
            "wire_type": CredentialType.API_KEY,
            "encrypted_secret": "enc-key",  # pragma: allowlist secret
            "location": CredentialLocation.QUERY,
            "field_name": "api_key",
        },
        2,
    ),
    (
        StoredCredentialType.BASIC_AUTH,
        _basic_child,
        {
            "wire_type": CredentialType.BASIC,
            "username": "alice",
            "encrypted_password": "enc-pw",  # pragma: allowlist secret
        },
        2,
    ),
    (
        StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
        _oauth_child,
        {
            "wire_type": CredentialType.OAUTH2,
            "encrypted_access_token": "enc-access",
            "encrypted_refresh_token": "enc-refresh",
            "token_expires_at": _EXPIRES,
            "provider_account_ref": "acct-1",
        },
        2,
    ),
    (
        StoredCredentialType.AWS_SIGV4,
        _sigv4_child,
        {
            "wire_type": CredentialType.SIGV4,
            "access_key_id": "AKIAEXAMPLE",
            "encrypted_secret_access_key": "enc-sak",  # pragma: allowlist secret
            "encrypted_session_token": "enc-st",
            "aws_region": "eu-west-1",
            "aws_service": "s3",
        },
        2,
    ),
]


async def _seed(
    ctx: Context,
    *,
    cred_id: str,
    stored_type: StoredCredentialType,
    children: Callable[[str], list[Any]],
    api_name: str | None = _API.name,
) -> None:
    async with ctx.control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=stored_type,
                name=f"name-{cred_id}",
                api_vendor=_VENDOR,
                api_name=api_name,
                api_version=_API.version if api_name else None,
                provider_account_ref="acct-1",
                created_by="usr_owner",
            )
        )
        await session.flush()
        session.add_all(children(cred_id))
        await session.commit()


async def _resolve_counted(ctx: Context, api: APIReference) -> tuple[ResolvedCredential, list[str]]:
    resolver = CredentialResolver(ctx)
    # Warm the pool so connection setup is not attributed to the resolve.
    await resolver.resolve(api=api, caller="agent_42")
    with _count_statements(ctx.control_db) as statements:
        resolved = await resolver.resolve(api=api, caller="agent_42")
    return resolved, statements


@pytest.mark.parametrize(
    ("stored_type", "children", "expected", "max_statements"),
    _CASES,
    ids=[case[0].value for case in _CASES],
)
async def test_resolve_issues_bounded_statements_per_type(
    integration_context: Context,
    clean_vendor: None,
    stored_type: StoredCredentialType,
    children: Callable[[str], list[Any]],
    expected: dict[str, Any],
    max_statements: int,
) -> None:
    """Each type resolves its own material in ≤ 2 statements (header + its child)."""
    cred_id = f"cred_1698_{stored_type.value.lower()}"[:30]
    await _seed(integration_context, cred_id=cred_id, stored_type=stored_type, children=children)

    resolved, statements = await _resolve_counted(integration_context, _API)

    assert len(statements) <= max_statements, statements
    assert resolved.credential_id == cred_id
    assert resolved.name == f"name-{cred_id}"
    assert resolved.created_by == "usr_owner"
    assert resolved.stored_type == stored_type
    for field, value in expected.items():
        assert getattr(resolved, field) == value, field


async def test_resolve_missing_child_row_resolves_without_material(
    integration_context: Context, clean_vendor: None
) -> None:
    """A bearer header with no token row still resolves (secret ``None``), as before."""
    await _seed(
        integration_context,
        cred_id="cred_1698_orphan",
        stored_type=StoredCredentialType.STATIC_BEARER_TOKEN,
        children=_no_child,
    )

    resolved, statements = await _resolve_counted(integration_context, _API)

    assert len(statements) <= 2, statements
    assert resolved.wire_type == CredentialType.BEARER_TOKEN
    assert resolved.encrypted_secret is None


async def test_resolve_mixed_vendor_loads_one_query_per_candidate_type(
    integration_context: Context, clean_vendor: None
) -> None:
    """Mixed-type candidates cost one child query per distinct type, not per table.

    The pinned API-key credential wins on specificity over the vendor-wide
    bearer one; both candidates' material is loaded in a single query each.
    """
    await _seed(
        integration_context,
        cred_id="cred_1698_mixed_bearer",
        stored_type=StoredCredentialType.STATIC_BEARER_TOKEN,
        children=_bearer_child,
        api_name=None,
    )
    await _seed(
        integration_context,
        cred_id="cred_1698_mixed_key",
        stored_type=StoredCredentialType.API_KEY,
        children=_api_key_child,
    )
    await _seed(
        integration_context,
        cred_id="cred_1698_mixed_noauth",
        stored_type=StoredCredentialType.NO_AUTH,
        children=_no_child,
        api_name="other-api",
    )

    resolved, statements = await _resolve_counted(integration_context, _API)

    assert len(statements) <= 3, statements
    assert resolved.credential_id == "cred_1698_mixed_key"
    assert resolved.encrypted_secret == "enc-key"  # pragma: allowlist secret
