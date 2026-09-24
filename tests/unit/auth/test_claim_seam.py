"""Unit tests for the claim-token minter seam (auth/core/claim.py)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jentic_one.auth.core import claim


@pytest.fixture(autouse=True)
def _restore_default_minter() -> Iterator[None]:
    """Keep the process-global from leaking between tests."""
    original = claim.get_claim_token_minter()
    yield
    claim.set_claim_token_minter(original)


def test_default_minter_returns_none() -> None:
    """OSS default: no claim token minted (single-user product behaviour)."""
    assert claim.get_claim_token_minter()("agnt_anything") is None


def test_installed_minter_is_used() -> None:
    claim.set_claim_token_minter(lambda agent_id: f"claim-for-{agent_id}")
    assert claim.get_claim_token_minter()("agnt_x") == "claim-for-agnt_x"


def test_last_write_wins() -> None:
    claim.set_claim_token_minter(lambda _a: "first")
    claim.set_claim_token_minter(lambda _a: "second")
    assert claim.get_claim_token_minter()("agnt_x") == "second"
