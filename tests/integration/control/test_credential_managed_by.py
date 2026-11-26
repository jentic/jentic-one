"""Integration tests for extension-managed credentials and the public credentials seam.

Real control and admin databases. Covers ``jentic_one.control.credentials_seam``
(header, material, managed create/update/delete), the HTTP 409
``credential_managed`` refusals on ``PATCH``/``DELETE /credentials/{id}``, the
refusal to bind a managed credential to an agent, and the broker resolver
never selecting a managed credential.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

import httpx
import pytest
from fastapi import FastAPI
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import CredentialNotBindableError
from jentic_one.broker.services.credentials.errors import (
    CredentialIdNotFoundError,
    CredentialNotProvisionedError,
)
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.control import credentials_seam as seam
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.web.app import get_exception_handlers
from jentic_one.control.web.deps import get_credential_service
from jentic_one.control.web.routers import credentials as credentials_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.schemas import APIReference as BrokerAPIReference
from jentic_one.shared.web import deps as shared_deps
from jentic_one.shared.web import get_ctx

pytestmark = pytest.mark.integration

_VENDOR = "managed-seam.example.com"
_OWNER = Identity(
    sub="usr_managed_owner",
    email="owner@example.com",
    permissions=["credentials:read", "credentials:write", "agents:write"],
)
_ADMIN = Identity(
    sub="usr_managed_admin",
    email="admin@example.com",
    permissions=["org:admin", "credentials:read", "credentials:write", "agents:write"],
)
_MARKER = seam.ManagedBy(kind="example_connection", ref="conn_one")
_OTHER_MARKER = seam.ManagedBy(kind="example_connection", ref="conn_two")
_API_KEY = "sk-managed-seam-secret-0001"  # pragma: allowlist secret
_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/managedSEAMEXAMPLE"  # pragma: allowlist secret


def _api_key_payload(name: str = "Managed key") -> seam.CredentialCreate:
    return seam.CredentialCreate(
        type=seam.CredentialType.API_KEY,
        name=name,
        api=seam.APIReference(vendor=_VENDOR, name="", version=""),
        key=_API_KEY,
        location="header",
        field_name="X-Api-Key",
    )


def _sigv4_payload(name: str = "Managed sigv4") -> seam.CredentialCreate:
    return seam.CredentialCreate(
        type=seam.CredentialType.SIGV4,
        name=name,
        api=seam.APIReference(vendor=_VENDOR, name="", version=""),
        access_key_id="AKIAMANAGEDSEAMEXAMPLE",  # pragma: allowlist secret
        secret_access_key=_SECRET_ACCESS_KEY,
        aws_region="eu-west-1",
        aws_service="execute-api",
    )


@pytest.fixture()
async def clean(integration_context: Context) -> AsyncGenerator[None, None]:
    ctx = integration_context

    async def _wipe() -> None:
        async with ctx.control_db.session() as session:
            rows = (
                (await session.execute(select(Credential).where(Credential.api_vendor == _VENDOR)))
                .scalars()
                .all()
            )
            ids = [row.id for row in rows]
            for row in rows:
                await session.delete(row)
            await session.commit()
        async with ctx.admin_db.session() as session:
            if ids:
                await session.execute(
                    delete(AgentCredentialBinding).where(
                        AgentCredentialBinding.credential_id.in_(ids)
                    )
                )
                await session.execute(delete(AuditEntry).where(AuditEntry.target_id.in_(ids)))
            await session.execute(delete(Agent).where(Agent.name.like("managed-seam-%")))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


def _app(ctx: Context, identity: Identity) -> FastAPI:
    app = FastAPI()
    app.include_router(credentials_router.router)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.dependency_overrides[get_ctx] = lambda: ctx
    app.dependency_overrides[get_credential_service] = lambda: CredentialService(ctx)
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: identity
    return app


@pytest.fixture()
def client_for(integration_context: Context) -> Iterator[object]:
    def _make(identity: Identity) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=_app(integration_context, identity))
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    yield _make


async def _audit(ctx: Context, credential_id: str) -> list[AuditEntry]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            select(AuditEntry).where(
                AuditEntry.target_type == AuditTargetType.CREDENTIAL.value,
                AuditEntry.target_id == credential_id,
            )
        )
        return list(result.scalars().all())


# --- create, header, list/get exposure -------------------------------------------------


async def test_create_managed_credential_sets_marker_and_shows_it(
    integration_context: Context, clean: None, client_for: object
) -> None:
    ctx = integration_context
    header = await seam.create_managed_credential(
        ctx, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )

    assert header.managed_by == _MARKER
    assert header.created_by == _OWNER.sub
    assert header.type == "api_key"
    assert header.active is True
    assert header.state == "connected"
    assert await seam.get_credential_header(ctx, header.id) == header

    async with client_for(_OWNER) as client:  # type: ignore[operator]
        got = await client.get(f"/credentials/{header.id}")
        listed = await client.get("/credentials")
    assert got.status_code == 200
    assert got.json()["managed_by"] == {"kind": "example_connection", "ref": "conn_one"}
    rows = {row["credential_id"]: row for row in listed.json()["data"]}
    assert rows[header.id]["managed_by"] == {"kind": "example_connection", "ref": "conn_one"}

    creates = [e for e in await _audit(ctx, header.id) if e.action == AuditAction.CREATE.value]
    assert len(creates) == 1
    assert creates[0].after is not None
    assert creates[0].after["managed_by"] == {"kind": "example_connection", "ref": "conn_one"}
    assert _API_KEY not in str(creates[0].after)


async def test_http_create_ignores_a_managed_by_field(
    integration_context: Context, clean: None, client_for: object
) -> None:
    async with client_for(_OWNER) as client:  # type: ignore[operator]
        resp = await client.post(
            "/credentials",
            json={
                "type": "api_key",
                "name": "Forged marker",
                "api": {"vendor": _VENDOR},
                "key": _API_KEY,
                "location": "header",
                "field_name": "X-Api-Key",
                "managed_by": {"kind": "example_connection", "ref": "conn_one"},
                "managed_by_kind": "example_connection",
                "managed_by_ref": "conn_one",
            },
        )
    assert resp.status_code == 201, resp.text
    credential_id = resp.json()["credential"]["credential_id"]
    assert resp.json()["credential"]["managed_by"] is None
    header = await seam.get_credential_header(integration_context, credential_id)
    assert header is not None
    assert header.managed_by is None


async def test_get_credential_header_missing_is_none(
    integration_context: Context, clean: None
) -> None:
    assert await seam.get_credential_header(integration_context, "cred_does_not_exist") is None


# --- HTTP edit/delete refusal ----------------------------------------------------------


async def test_http_patch_and_delete_of_managed_credential_are_409_for_owner(
    integration_context: Context, clean: None, client_for: object
) -> None:
    header = await seam.create_managed_credential(
        integration_context, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )
    async with client_for(_OWNER) as client:  # type: ignore[operator]
        patched = await client.patch(
            f"/credentials/{header.id}", json={"type": "api_key", "name": "Renamed"}
        )
        deleted = await client.delete(f"/credentials/{header.id}")
    assert patched.status_code == 409
    assert patched.json()["type"].endswith("credential_managed")
    assert deleted.status_code == 409
    assert deleted.json()["type"].endswith("credential_managed")

    after = await seam.get_credential_header(integration_context, header.id)
    assert after is not None
    assert after.name == "Managed key"


async def test_http_patch_of_managed_credential_is_409_for_admin_but_delete_succeeds(
    integration_context: Context, clean: None, client_for: object
) -> None:
    header = await seam.create_managed_credential(
        integration_context, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )
    async with client_for(_ADMIN) as client:  # type: ignore[operator]
        patched = await client.patch(
            f"/credentials/{header.id}", json={"type": "api_key", "name": "Renamed"}
        )
        deleted = await client.delete(f"/credentials/{header.id}")
    assert patched.status_code == 409
    assert patched.json()["type"].endswith("credential_managed")
    assert deleted.status_code == 204
    assert await seam.get_credential_header(integration_context, header.id) is None


async def test_http_edit_of_unmanaged_credential_is_unaffected(
    integration_context: Context, clean: None, client_for: object
) -> None:
    view = await CredentialService(integration_context).create(
        _api_key_payload("Plain key"), identity=_OWNER
    )
    async with client_for(_OWNER) as client:  # type: ignore[operator]
        patched = await client.patch(
            f"/credentials/{view.credential_id}", json={"type": "api_key", "name": "Renamed"}
        )
        deleted = await client.delete(f"/credentials/{view.credential_id}")
    assert patched.status_code == 200
    assert deleted.status_code == 204


# --- seam update/delete ----------------------------------------------------------------


async def test_seam_update_needs_the_matching_marker_and_audits_rotation(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    header = await seam.create_managed_credential(
        ctx, _sigv4_payload(), identity=_OWNER, managed_by=_MARKER
    )
    rotation = seam.CredentialUpdate(
        type=seam.CredentialType.SIGV4,
        access_key_id="AKIAROTATEDSEAMEXAMPL",  # pragma: allowlist secret
        secret_access_key="rotated/secret/value/EXAMPLE0000000000000",  # pragma: allowlist secret
    )

    with pytest.raises(seam.CredentialManagedError):
        await seam.update_managed_credential(
            ctx, header.id, rotation, identity=_OWNER, managed_by=_OTHER_MARKER
        )
    with pytest.raises(seam.CredentialManagedError):
        await CredentialService(ctx).update(header.id, rotation, identity=_OWNER)

    updated = await seam.update_managed_credential(
        ctx, header.id, rotation, identity=_OWNER, managed_by=_MARKER
    )
    assert updated.id == header.id
    assert updated.managed_by == _MARKER
    assert updated.revision > header.revision

    material = await seam.resolve_credential_material(
        ctx, header.id, expected_type=seam.CredentialType.SIGV4
    )
    assert isinstance(material, seam.SigV4Material)
    assert material.access_key_id == "AKIAROTATEDSEAMEXAMPL"  # pragma: allowlist secret
    assert material.secret_access_key == "rotated/secret/value/EXAMPLE0000000000000"

    updates = [e for e in await _audit(ctx, header.id) if e.action == AuditAction.UPDATE.value]
    assert len(updates) == 1
    assert updates[0].after is not None
    assert updates[0].after["secret_access_key_rotated"] is True
    assert "rotated/secret" not in str(updates[0].after)


async def test_seam_update_refuses_an_unmanaged_credential(
    integration_context: Context, clean: None
) -> None:
    view = await CredentialService(integration_context).create(
        _api_key_payload("Plain key"), identity=_OWNER
    )
    with pytest.raises(seam.CredentialManagedError):
        await seam.update_managed_credential(
            integration_context,
            view.credential_id,
            seam.CredentialUpdate(type=seam.CredentialType.API_KEY, name="x"),
            identity=_OWNER,
            managed_by=_MARKER,
        )


async def test_seam_delete_needs_the_matching_marker(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    header = await seam.create_managed_credential(
        ctx, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )
    with pytest.raises(seam.CredentialManagedError):
        await seam.delete_managed_credential(
            ctx, header.id, identity=_OWNER, managed_by=_OTHER_MARKER
        )
    assert await seam.get_credential_header(ctx, header.id) is not None

    assert await seam.delete_managed_credential(ctx, header.id, identity=_OWNER, managed_by=_MARKER)
    assert await seam.get_credential_header(ctx, header.id) is None
    # Already gone.
    assert not await seam.delete_managed_credential(
        ctx, header.id, identity=_OWNER, managed_by=_MARKER
    )


async def test_seam_mutations_keep_owner_scoping(integration_context: Context, clean: None) -> None:
    ctx = integration_context
    header = await seam.create_managed_credential(
        ctx, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )
    stranger = Identity(sub="usr_stranger", permissions=["credentials:write"])
    with pytest.raises(seam.CredentialNotFoundError):
        await seam.update_managed_credential(
            ctx,
            header.id,
            seam.CredentialUpdate(type=seam.CredentialType.API_KEY, name="x"),
            identity=stranger,
            managed_by=_MARKER,
        )
    assert not await seam.delete_managed_credential(
        ctx, header.id, identity=stranger, managed_by=_MARKER
    )


# --- material --------------------------------------------------------------------------


async def test_resolve_credential_material_api_key(
    integration_context: Context, clean: None
) -> None:
    view = await CredentialService(integration_context).create(
        _api_key_payload("Plain key"), identity=_OWNER
    )
    material = await seam.resolve_credential_material(
        integration_context, view.credential_id, expected_type=seam.CredentialType.API_KEY
    )
    assert isinstance(material, seam.ApiKeyMaterial)
    assert material.key == _API_KEY
    assert material.field_name == "X-Api-Key"
    assert material.location == "header"
    assert _API_KEY not in repr(material)


async def test_resolve_credential_material_refuses_unusable_credentials(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    svc = CredentialService(ctx)
    inactive = await svc.create(_api_key_payload("Inactive"), identity=_OWNER)
    await svc.update(
        inactive.credential_id,
        seam.CredentialUpdate(type=seam.CredentialType.API_KEY, active=False),
        identity=_OWNER,
    )
    pending = await svc.create(_api_key_payload("Pending"), identity=_OWNER)
    async with ctx.control_db.transaction() as session:
        await CredentialRepository.set_state(session, pending.credential_id, "pending")
    sigv4 = await svc.create(_sigv4_payload("Wrong type"), identity=_OWNER)

    cases = {
        inactive.credential_id: "inactive",
        pending.credential_id: "not connected",
        sigv4.credential_id: "not a api_key credential",
        "cred_missing": "not found",
    }
    for credential_id, reason in cases.items():
        with pytest.raises(seam.CredentialUnavailableError) as exc_info:
            await seam.resolve_credential_material(
                ctx, credential_id, expected_type=seam.CredentialType.API_KEY
            )
        assert exc_info.value.reason == reason

    with pytest.raises(ValueError, match="No material"):
        await seam.resolve_credential_material(
            ctx, sigv4.credential_id, expected_type=seam.CredentialType.BASIC
        )


# --- binding ---------------------------------------------------------------------------


async def _seed_agent(ctx: Context) -> str:
    agent = Agent(name="managed-seam-agent", registered_by=_ADMIN.sub, status="active")
    async with ctx.admin_db.session() as session:
        session.add(agent)
        await session.commit()
        return agent.id


async def _binding_count(ctx: Context, credential_id: str) -> int:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(AgentCredentialBinding).where(
                AgentCredentialBinding.credential_id == credential_id
            )
        )
        return len(rows.scalars().all())


async def test_binding_a_managed_credential_is_refused(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    agent_id = await _seed_agent(ctx)
    header = await seam.create_managed_credential(
        ctx, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )
    # Even org:admin, who may bind any credential, cannot bind a managed one.
    with pytest.raises(CredentialNotBindableError):
        await AgentService(ctx).bind_credential(agent_id, credential_id=header.id, identity=_ADMIN)
    assert await _binding_count(ctx, header.id) == 0

    # An unmanaged credential still binds.
    plain = await CredentialService(ctx).create(_api_key_payload("Plain key"), identity=_OWNER)
    await AgentService(ctx).bind_credential(
        agent_id, credential_id=plain.credential_id, identity=_ADMIN
    )
    assert await _binding_count(ctx, plain.credential_id) == 1


async def test_managed_credential_is_not_an_existing_connect_candidate(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    managed = await seam.create_managed_credential(
        ctx, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )
    plain = await CredentialService(ctx).create(_api_key_payload("Plain key"), identity=_OWNER)
    async with ctx.control_db.session() as session:
        rows = await CredentialRepository.list_connected_covering(
            session,
            vendor=plain.api.vendor,
            name=None,
            version=None,
            account_ref_providers=(),
        )
    ids = {row.id for row in rows}
    assert plain.credential_id in ids
    assert managed.id not in ids


# --- broker resolver -------------------------------------------------------------------


async def test_broker_resolver_never_selects_a_managed_credential(
    integration_context: Context, clean: None
) -> None:
    ctx = integration_context
    managed = await seam.create_managed_credential(
        ctx, _api_key_payload(), identity=_OWNER, managed_by=_MARKER
    )
    api = BrokerAPIReference(vendor=_VENDOR, name="any", version="v1")
    resolver = CredentialResolver(ctx)

    # The only covering credential is managed: nothing is provisioned, even
    # when the caller is "bound" to it and names it explicitly.
    with pytest.raises(CredentialNotProvisionedError):
        await resolver.resolve(api=api, caller="agent_x", allowed_credential_ids=[managed.id])
    with pytest.raises(CredentialNotProvisionedError):
        await resolver.resolve(
            api=api,
            caller="agent_x",
            credential_id=managed.id,
            allowed_credential_ids=[managed.id],
        )

    plain = await CredentialService(ctx).create(_api_key_payload("Plain key"), identity=_OWNER)
    resolved = await resolver.resolve(
        api=api, caller="agent_x", allowed_credential_ids=[managed.id, plain.credential_id]
    )
    assert resolved.credential_id == plain.credential_id
    with pytest.raises(CredentialIdNotFoundError):
        await resolver.resolve(
            api=api,
            caller="agent_x",
            credential_id=managed.id,
            allowed_credential_ids=[managed.id, plain.credential_id],
        )
    with pytest.raises(CredentialIdNotFoundError):
        await resolver.resolve(api=api, caller="agent_x", credential_id=managed.id)
