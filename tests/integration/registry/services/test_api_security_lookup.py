"""The registry side of ``SecuritySchemesLookupProtocol`` (real registry DB).

Pins what control's API-target connect sessions read: the live revision's
declared schemes (with OAuth scopes and endpoints), the origin set its servers
can target (host variables expanded over default + enum), the host variables
that have no enum, and the revision's provenance. Also pins that the
server-host change guard holds a host change while an API-target connect
session is open.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, text, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.services.api_security_lookup_service import ApiSecurityLookupService
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionState

pytestmark = pytest.mark.integration

_VENDOR = "lookup-example"
_NAME = "widgets"
_VERSION = "1.0.0"


def _source(servers: list[dict[str, Any]], schemes: dict[str, Any]) -> dict[str, Any]:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Widgets", "version": _VERSION, "description": uuid.uuid4().hex},
        "servers": servers,
        "components": {"securitySchemes": schemes},
        "paths": {
            "/widgets": {
                "get": {"operationId": "list", "responses": {"200": {"description": "OK"}}}
            }
        },
    }
    return {
        "type": "inline",
        "content": json.dumps(spec),
        "filename": "openapi.json",
        "vendor": _VENDOR,
        "api_name": _NAME,
        "version": _VERSION,
        "origin": "catalog",
        "submitted_by": "usr_writer",
    }


async def _import(ctx: Context, source: dict[str, Any]) -> dict[str, Any]:
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        session=None,
        payload={"sources": [source]},
        created_by="usr_writer",
    )
    revision: dict[str, Any] = result.body["revisions"][0]
    return revision


@pytest.fixture()
async def clean(
    registry_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with registry_db.session() as session:
            for table in (
                OperationURLIndex,
                SecuritySchemeFlow,
                SecurityScheme,
                ServerVariable,
                Server,
                Operation,
                SpecFile,
            ):
                await session.execute(delete(table))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(
                text("DELETE FROM connect_sessions WHERE vendor = :v"), {"v": _VENDOR}
            )
            await session.execute(
                text("DELETE FROM credentials WHERE api_vendor = :v"), {"v": _VENDOR}
            )
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def test_lookup_returns_schemes_hosts_and_provenance(
    integration_context: Context, clean: None
) -> None:
    rev = await _import(
        integration_context,
        _source(
            [
                {
                    "url": "https://{region}.widgets.example/v1",
                    "variables": {"region": {"default": "eu", "enum": ["eu", "us"]}},
                }
            ],
            {
                "key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
                "bearer": {"type": "http", "scheme": "Bearer"},
                "oauth": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://auth.widgets.example/authorize",
                            "tokenUrl": "https://auth.widgets.example/token",
                            "scopes": {"read": "Read", "write": "Write"},
                        }
                    },
                },
            },
        ),
    )
    view = await ApiSecurityLookupService(integration_context).lookup(
        vendor=_VENDOR, name=_NAME, version=_VERSION
    )
    assert view is not None
    assert view.revision_id == rev["revision_id"]
    assert view.hosts == ("https://eu.widgets.example", "https://us.widgets.example")
    assert view.unpinned_host_variables == ()
    by_name = {s.name: s for s in view.schemes}
    assert (by_name["key"].type, by_name["key"].location, by_name["key"].field_name) == (
        "apiKey",
        "header",
        "X-Api-Key",
    )
    assert by_name["bearer"].http_scheme == "bearer"
    assert by_name["oauth"].oauth_scopes == ("read", "write")
    assert by_name["oauth"].token_url == "https://auth.widgets.example/token"
    assert view.provenance.origin == "catalog"
    assert view.provenance.submitted_by == "usr_writer"

    assert (
        await ApiSecurityLookupService(integration_context).lookup(
            vendor=_VENDOR, name="missing", version=_VERSION
        )
        is None
    )


async def test_lookup_reports_host_variables_without_enum(
    integration_context: Context, clean: None
) -> None:
    await _import(
        integration_context,
        _source(
            [
                {
                    "url": "https://{tenant}.widgets.example",
                    "variables": {"tenant": {"default": "a"}},
                }
            ],
            {"key": {"type": "apiKey", "in": "query", "name": "key"}},
        ),
    )
    view = await ApiSecurityLookupService(integration_context).lookup(
        vendor=_VENDOR, name=_NAME, version=_VERSION
    )
    assert view is not None
    assert view.unpinned_host_variables == ("tenant",)


async def test_open_api_target_session_holds_a_host_change(
    integration_context: Context, control_db: DatabaseSession, clean: None
) -> None:
    schemes = {"key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}
    base = await _import(
        integration_context, _source([{"url": "https://old.widgets.example"}], schemes)
    )
    credential_id = generate_ksuid("cred")
    async with control_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, api_name, api_version, state)"
                " VALUES (:id, 'API_KEY', 'w', :v, :n, :ver, 'pending')"
            ),
            {"id": credential_id, "v": _VENDOR, "n": _NAME, "ver": _VERSION},
        )
        await session.execute(
            text(
                "INSERT INTO connect_sessions (id, credential_id, target_kind, vendor, api_name,"
                " api_version, agent_id, initiator_actor_id, state, resolved_flow, poll_token)"
                " VALUES (:id, :cid, 'api', :v, :n, :ver, 'agnt_x', 'agnt_x', 'created',"
                " 'manual_api_key', 'digest-lookup')"
            ),
            {
                "id": generate_ksuid("cs"),
                "cid": credential_id,
                "v": _VENDOR,
                "n": _NAME,
                "ver": _VERSION,
            },
        )

    moved = await _import(
        integration_context, _source([{"url": "https://new.widgets.example"}], schemes)
    )
    assert moved["state"] == ApiRevisionState.DRAFT
    assert moved["held_for_review"] is True
    view = await ApiSecurityLookupService(integration_context).lookup(
        vendor=_VENDOR, name=_NAME, version=_VERSION
    )
    assert view is not None and view.revision_id == base["revision_id"]
