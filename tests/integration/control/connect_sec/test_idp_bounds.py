"""IdP exchanges are bounded: one stalled vendor cannot stall other device flows.

The device-flow scanner advances every due credential in one tick. A vendor
token endpoint that never finishes its response is abandoned at the deadline,
so another credential's flow still reaches ``connected`` and the TTL sweep keeps
running. The vendor is faked at the IdP transport seam; the control DB is real.
"""

from __future__ import annotations

import asyncio
from functools import partial
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl

import httpx
import pytest

from jentic_one.control.repos import CredentialRepository
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.services.credentials.connect_service import ConnectService
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectRequest,
    DeviceAuthorizationChallenge,
)
from jentic_one.control.services.integrations import device_authorization as df
from jentic_one.shared.context import Context
from jentic_one.shared.http.idp_client import idp_request
from jentic_one.shared.jobs import connect_poll_scanner
from jentic_one.shared.jobs.connect_poll_scanner import ConnectPollScanner
from jentic_one.shared.models.credentials import CredentialType
from tests.integration.control.connect_sec.support import OWNER, OWNER_ID

pytestmark = pytest.mark.integration

_TARPIT_CODE = "device-code-tarpit"
_HEALTHY_CODE = "device-code-healthy"
_DEADLINE_SECONDS = 0.5


async def _device_credential(ctx: Context, name: str) -> str:
    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type=CredentialType.OAUTH2,
            name=name,
            api_vendor="foo",
            api_name="bar",
            api_version="v1",
            provider="device_authorization",
            created_by=OWNER_ID,
            state="pending",
        )
        await DeviceAuthorizationCredentialRepository.create(
            session,
            credential_id=credential.id,
            client_id="public-client",
            token_url="https://idp.example.com/token",
            authorization_endpoint="https://idp.example.com/device/code",
            requested_scopes=["read"],
            created_by=OWNER_ID,
        )
        return credential.id


async def _begin(ctx: Context, credential_id: str, device_code: str) -> None:
    result = df.BeginResult(
        device_code=device_code,
        user_code=device_code.upper(),
        verification_uri="https://idp.example.com/device",
        verification_uri_complete=None,
        expires_in=900,
        interval=1,
    )
    with patch.object(df, "begin_device_authorization", new=AsyncMock(return_value=result)):
        challenge = await ConnectService(ctx).begin(
            credential_id, ConnectRequest(scopes=["read"]), identity=OWNER
        )
    assert isinstance(challenge, DeviceAuthorizationChallenge)


async def test_device_connect_against_a_tarpit_endpoint_does_not_stall_other_flows(
    env: Context,
) -> None:
    ctx = env
    tarpit_id = await _device_credential(ctx, "tarpit vendor")
    healthy_id = await _device_credential(ctx, "healthy vendor")
    await _begin(ctx, tarpit_id, _TARPIT_CODE)
    await _begin(ctx, healthy_id, _HEALTHY_CODE)

    async def vendor(request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode()))
        if form.get("device_code") == _TARPIT_CODE:
            await asyncio.sleep(3600)
        return httpx.Response(200, json={"access_token": "at-healthy", "expires_in": 3600})

    scanner = ConnectPollScanner(ctx)
    with (
        patch.object(connect_poll_scanner, "_ADVANCE_DEADLINE_SECONDS", 2.0),
        patch.object(df, "idp_request", partial(idp_request, deadline_s=_DEADLINE_SECONDS)),
        patch(
            "jentic_one.shared.http.idp_client.build_always_pinned_transport",
            return_value=httpx.MockTransport(vendor),
        ),
    ):
        # The healthy flow connects within two ticks even though the tarpit
        # flow's poll never completes on its own.
        for _ in range(2):
            await asyncio.wait_for(scanner._tick(), 15)
            async with ctx.control_db.session() as session:
                healthy = await CredentialRepository.get_by_id(session, healthy_id)
            assert healthy is not None
            if healthy.state == "connected":
                break
            await asyncio.sleep(1.1)  # the poll interval

    assert healthy is not None
    assert healthy.state == "connected"
    async with ctx.control_db.session() as session:
        tarpit = await CredentialRepository.get_by_id(session, tarpit_id)
    assert tarpit is not None
    assert tarpit.state != "connected"
