"""Shared fixtures for the auth unit suite."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture()
def no_api_key() -> Iterator[AsyncMock]:
    """Stub ``AgentCredentialRepository.has_api_key`` to report no key.

    ``AgentService`` fills ``AgentView.has_api_key`` on every single-agent
    response. Unit tests that drive it with a mock session need this lookup
    stubbed, as it would otherwise run a query against the mock.
    """
    with patch(
        "jentic_one.auth.services.agent_service.AgentCredentialRepository.has_api_key",
        new_callable=AsyncMock,
        return_value=False,
    ) as mock:
        yield mock
