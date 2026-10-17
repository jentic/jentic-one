"""Statement budget and read boundary for broker credential resolution (#1698).

``CredentialResolver.resolve`` runs on every broker call. These tests seed
credentials in the real control DB, resolve, and record every SQL statement the
control engine issues: one header query for the vendor's candidates, then at
most one secret-material query — for the single winning credential only, and
none for ``NO_AUTH`` or when resolution fails (404/409). They also pin that each
type still resolves to the same material, and that the broker never selects
child rows of credentials outside the caller's ``allowed_credential_ids``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, event, text
from sqlalchemy.exc import InvalidRequestError

from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialNotProvisionedError,
)
from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.repos import CredentialRepository
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import StoredCredentialType
from jentic_one.shared.models.credentials import CredentialLocation, CredentialType
from jentic_one.shared.schemas import APIReference
from tests.harness.db_round_trips import trace_round_trips

pytestmark = pytest.mark.integration

_VENDOR = "perf-1698-vendor"
_API = APIReference(vendor=_VENDOR, name="payments", version="v1")
_EXPIRES = datetime(2030, 1, 1, tzinfo=UTC)
# ``FROM credentials`` / ``FROM control.credentials`` / quoted variants.
_HEADER_FROM = re.compile(r'\bFROM\s+(?:"?\w+"?\.)?"?credentials"?(?:\s|$)', re.IGNORECASE)


@dataclass(frozen=True)
class _Statement:
    sql: str
    params: tuple[Any, ...]

    @property
    def is_header(self) -> bool:
        """The ``list_by_vendor`` candidate query (``FROM credentials``)."""
        return _HEADER_FROM.search(self.sql) is not None


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


def _flatten(parameters: Any) -> tuple[Any, ...]:
    if isinstance(parameters, dict):
        return tuple(parameters.values())
    if isinstance(parameters, list | tuple):
        return tuple(parameters)
    return ()


@contextmanager
def _record_statements(db: DatabaseSession) -> Iterator[list[_Statement]]:
    statements: list[_Statement] = []

    def _record(_conn: Any, _cursor: Any, sql: str, parameters: Any, *_rest: Any) -> None:
        statements.append(_Statement(sql=sql, params=_flatten(parameters)))

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
    (StoredCredentialType.NO_AUTH, _no_child, {"wire_type": CredentialType.NO_AUTH}, 0),
    (
        StoredCredentialType.STATIC_BEARER_TOKEN,
        _bearer_child,
        {
            "wire_type": CredentialType.BEARER_TOKEN,
            "encrypted_secret": "enc-bearer",  # pragma: allowlist secret
        },
        1,
    ),
    (
        StoredCredentialType.SESSION_TOKEN,
        _bearer_child,
        {
            "wire_type": CredentialType.BEARER_TOKEN,
            "encrypted_secret": "enc-bearer",  # pragma: allowlist secret
        },
        1,
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
        1,
    ),
    (
        StoredCredentialType.BASIC_AUTH,
        _basic_child,
        {
            "wire_type": CredentialType.BASIC,
            "username": "alice",
            "encrypted_password": "enc-pw",  # pragma: allowlist secret
        },
        1,
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
        1,
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
        1,
    ),
]


async def _seed(
    ctx: Context,
    *,
    cred_id: str,
    stored_type: StoredCredentialType,
    children: Callable[[str], list[Any]],
    api_name: str | None = _API.name,
    active: bool = True,
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
                active=active,
            )
        )
        await session.flush()
        session.add_all(children(cred_id))
        await session.commit()


@contextmanager
def _counted(ctx: Context) -> Iterator[list[_Statement]]:
    with _record_statements(ctx.control_db) as statements:
        yield statements


async def _warm_pool(ctx: Context) -> None:
    """Open a pooled connection so its setup is not attributed to the resolve."""
    async with ctx.control_db.session() as session:
        await session.execute(text("SELECT 1"))


async def _resolve_counted(
    ctx: Context,
    api: APIReference,
    *,
    allowed_credential_ids: Collection[str] | None = None,
) -> tuple[ResolvedCredential, list[_Statement]]:
    await _warm_pool(ctx)
    with _counted(ctx) as statements:
        resolved = await CredentialResolver(ctx).resolve(
            api=api, caller="agent_42", allowed_credential_ids=allowed_credential_ids
        )
    return resolved, statements


def _child_statements(statements: list[_Statement]) -> list[_Statement]:
    headers = [s for s in statements if s.is_header]
    assert len(headers) == 1, statements
    return [s for s in statements if not s.is_header]


@pytest.mark.parametrize(
    ("stored_type", "children", "expected", "child_statements"),
    _CASES,
    ids=[case[0].value for case in _CASES],
)
async def test_resolve_issues_bounded_statements_per_type(
    integration_context: Context,
    clean_vendor: None,
    stored_type: StoredCredentialType,
    children: Callable[[str], list[Any]],
    expected: dict[str, Any],
    child_statements: int,
) -> None:
    """Each type costs the header + its own child query (none for ``NO_AUTH``)."""
    cred_id = f"cred_1698_{stored_type.value.lower()}"[:30]
    await _seed(integration_context, cred_id=cred_id, stored_type=stored_type, children=children)

    resolved, statements = await _resolve_counted(integration_context, _API)

    child = _child_statements(statements)
    assert len(child) == child_statements, statements
    for stmt in child:
        assert cred_id in stmt.params, stmt
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

    assert len(_child_statements(statements)) == 1, statements
    assert resolved.wire_type == CredentialType.BEARER_TOKEN
    assert resolved.encrypted_secret is None


async def _seed_mixed_vendor(ctx: Context) -> None:
    """A vendor with one credential of every material-bearing type, plus decoys.

    - ``cred_1698_win``  API key, pinned, bound — the winner.
    - ``cred_1698_wild`` bearer, vendor-wide, bound — loses on specificity.
    - ``cred_1698_oauth`` OAuth token, pinned, **unbound**.
    - ``cred_1698_basic`` basic auth, pinned, bound but **inactive**.
    - ``cred_1698_sigv4`` SigV4, pinned to another API, bound — does not cover.
    """
    await _seed(
        ctx,
        cred_id="cred_1698_win",
        stored_type=StoredCredentialType.API_KEY,
        children=_api_key_child,
    )
    await _seed(
        ctx,
        cred_id="cred_1698_wild",
        stored_type=StoredCredentialType.STATIC_BEARER_TOKEN,
        children=_bearer_child,
        api_name=None,
    )
    await _seed(
        ctx,
        cred_id="cred_1698_oauth",
        stored_type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
        children=_oauth_child,
    )
    await _seed(
        ctx,
        cred_id="cred_1698_basic",
        stored_type=StoredCredentialType.BASIC_AUTH,
        children=_basic_child,
        active=False,
    )
    await _seed(
        ctx,
        cred_id="cred_1698_sigv4",
        stored_type=StoredCredentialType.AWS_SIGV4,
        children=_sigv4_child,
        api_name="other-api",
    )


_MIXED_IDS = {
    "cred_1698_win",
    "cred_1698_wild",
    "cred_1698_oauth",
    "cred_1698_basic",
    "cred_1698_sigv4",
}
_MIXED_BOUND = _MIXED_IDS - {"cred_1698_oauth"}


async def test_resolve_mixed_vendor_loads_only_the_winners_material(
    integration_context: Context, clean_vendor: None
) -> None:
    """Five candidates of five types → exactly one child query, for the winner."""
    await _seed_mixed_vendor(integration_context)

    resolved, statements = await _resolve_counted(
        integration_context, _API, allowed_credential_ids=_MIXED_BOUND
    )

    child = _child_statements(statements)
    assert len(child) == 1, statements
    assert "customer_api_keys" in child[0].sql
    assert set(child[0].params) & _MIXED_IDS == {"cred_1698_win"}
    assert resolved.credential_id == "cred_1698_win"
    assert resolved.encrypted_secret == "enc-key"  # pragma: allowlist secret


async def test_resolve_never_selects_child_rows_outside_allowed_credential_ids(
    integration_context: Context, clean_vendor: None
) -> None:
    """Unbound credentials' secret rows are never selected — even the would-be winner's.

    The unbound OAuth credential is pinned (most specific) and would win without
    the binding boundary; bound only to the vendor-wide bearer credential, the
    broker reads that one row and nothing else.
    """
    await _seed_mixed_vendor(integration_context)
    allowed = {"cred_1698_wild"}

    resolved, statements = await _resolve_counted(
        integration_context, _API, allowed_credential_ids=allowed
    )

    assert resolved.credential_id == "cred_1698_wild"
    assert resolved.encrypted_secret == "enc-bearer"  # pragma: allowlist secret
    child = _child_statements(statements)
    assert len(child) == 1, statements
    assert "token_value_credentials" in child[0].sql
    for stmt in child:
        assert set(stmt.params) & _MIXED_IDS <= allowed, stmt


@pytest.mark.parametrize(
    ("allowed", "error"),
    [
        # Bound to nothing: deny-all boundary.
        (set(), CredentialNotProvisionedError),
        # Bound only to the inactive and the non-covering credentials.
        ({"cred_1698_basic", "cred_1698_sigv4"}, CredentialNotProvisionedError),
        # Bound to two equally specific covering credentials.
        ({"cred_1698_win", "cred_1698_tie"}, AmbiguousCredentialError),
    ],
    ids=["404-deny-all", "404-inactive-or-non-covering", "409-ambiguous"],
)
async def test_resolve_failure_selects_no_child_rows(
    integration_context: Context,
    clean_vendor: None,
    allowed: set[str],
    error: type[Exception],
) -> None:
    """A 404 or 409 costs the header query only — no secret row is read."""
    await _seed_mixed_vendor(integration_context)
    await _seed(
        integration_context,
        cred_id="cred_1698_tie",
        stored_type=StoredCredentialType.STATIC_BEARER_TOKEN,
        children=_bearer_child,
    )
    await _warm_pool(integration_context)

    with _counted(integration_context) as statements, pytest.raises(error):
        await CredentialResolver(integration_context).resolve(
            api=_API, caller="agent_42", allowed_credential_ids=allowed
        )

    assert _child_statements(statements) == [], statements


async def test_list_by_vendor_is_header_only_and_unloaded_children_raise(
    integration_context: Context, clean_vendor: None
) -> None:
    """``list_by_vendor`` loads no material; an unloaded child raises, never lazy-loads."""
    await _seed_mixed_vendor(integration_context)

    async with integration_context.control_db.session() as session:
        with _counted(integration_context) as statements:
            candidates = await CredentialRepository.list_by_vendor(session, _VENDOR)
        assert len(statements) == 1, statements
        by_id = {c.id: c for c in candidates}
        assert set(by_id) == _MIXED_IDS

        winner = by_id["cred_1698_win"]
        with pytest.raises(InvalidRequestError):
            _ = winner.customer_api_key

        with _counted(integration_context) as statements:
            await CredentialRepository.load_material(session, [winner])
        assert len(statements) == 1, statements
        assert winner.customer_api_key is not None
        assert winner.customer_api_key.encrypted_key == "enc-key"  # pragma: allowlist secret
        # The winner's other children and every other candidate's stay unloaded.
        with pytest.raises(InvalidRequestError):
            _ = winner.token_value_credential
        with pytest.raises(InvalidRequestError):
            _ = by_id["cred_1698_oauth"].oauth_token


@pytest.mark.parametrize("outcome", ["resolved", "not-provisioned"])
async def test_resolve_reads_without_begin_or_rollback(
    integration_context: Context, clean_vendor: None, outcome: str
) -> None:
    """Resolution runs in autocommit: no BEGIN/ROLLBACK round trips (#1823).

    The resolved case runs the header and material queries; the 404 case
    raises out of the session, which must not issue a ROLLBACK either.
    """
    await _seed_mixed_vendor(integration_context)
    await _warm_pool(integration_context)
    allowed = {"cred_1698_win"} if outcome == "resolved" else set()

    with trace_round_trips(integration_context.control_db) as trace:
        if outcome == "resolved":
            resolved = await CredentialResolver(integration_context).resolve(
                api=_API, caller="agent_42", allowed_credential_ids=allowed
            )
            assert resolved.credential_id == "cred_1698_win"
        else:
            with pytest.raises(CredentialNotProvisionedError):
                await CredentialResolver(integration_context).resolve(
                    api=_API, caller="agent_42", allowed_credential_ids=allowed
                )

    assert len(trace.statements) == (2 if outcome == "resolved" else 1), trace.statements
    trace.assert_no_begin_or_rollback()
