"""Unit tests for the shared permission-expansion helper."""

from __future__ import annotations

from jentic_one.shared.auth.permissions import has_effective_permission


def test_direct_grant_matches() -> None:
    assert has_effective_permission(["overlays:confirm"], "overlays:confirm") is True


def test_missing_grant_does_not_match() -> None:
    assert has_effective_permission(["catalog:import"], "overlays:confirm") is False


def test_org_admin_expands_to_confirm() -> None:
    # org:admin implies overlays:confirm via the implication map.
    assert has_effective_permission(["org:admin"], "overlays:confirm") is True


def test_empty_grants() -> None:
    assert has_effective_permission([], "overlays:confirm") is False
