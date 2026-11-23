"""Shared fixtures for the auth unit suite."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.auth.core import claim
from jentic_one.auth.core.idp import (
    get_admission_policy,
    get_default_idp_grants,
    no_default_grants,
    open_admission_policy,
    set_admission_policy,
    set_default_idp_grants,
)


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


@pytest.fixture()
def stock_claim_minter() -> Iterator[None]:
    """Install the stock claim-token minter (mints nothing) for one test.

    The minter is process-global and another deployment may register its own at
    import, so tests that assert the stock behaviour install it explicitly and
    restore whatever was installed before.
    """
    original = claim.get_claim_token_minter()
    claim.set_claim_token_minter(claim.no_claim_token)
    try:
        yield
    finally:
        claim.set_claim_token_minter(original)


@pytest.fixture()
def stock_idp_seams() -> Iterator[None]:
    """Install the stock IdP admission policy and default-grants provider.

    Restores the previously installed policy and provider afterwards.
    """
    original_policy = get_admission_policy()
    original_grants = get_default_idp_grants()
    set_admission_policy(open_admission_policy)
    set_default_idp_grants(no_default_grants)
    try:
        yield
    finally:
        set_admission_policy(original_policy)
        set_default_idp_grants(original_grants)
