"""Fixtures for the smoke-upstream harness self-tests.

These tests are DB-less and auth-less: they exercise the harness app directly
over ``httpx.ASGITransport`` (Mode 1 — in-memory ASGI) and never reach the
broker or a database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.harness.smoke_upstream.app import build_smoke_app
from tests.harness.smoke_upstream.mock_control import reset_sequences

SMOKE_BASE_URL = "http://smoke-corp.local"


@pytest.fixture
def smoke_app() -> FastAPI:
    return build_smoke_app()


@pytest.fixture
async def smoke_client(smoke_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=smoke_app)
    async with AsyncClient(transport=transport, base_url=SMOKE_BASE_URL) as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_mock_sequences() -> Iterator[None]:
    reset_sequences()
    yield
    reset_sequences()
