"""Retired-scope tolerance (theme-5 Phase 5b).

The toolkit scopes are out of the catalogue, but stored grants still carry
them: user permission rows, agent ``actor_scope_grants``, and filed
access-request ``scope:grant`` items written before the retirement. Every
validation path that rejects unknown scopes must accept-and-ignore
``RETIRED_SCOPES`` members so a re-submit of a stored grant never 422s.
"""

from __future__ import annotations

import pytest

from jentic_one.control.services.access_requests.errors import (
    UnsupportedScopeGrantError,
    assert_grantable_scope,
)
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, IMPLICATION_MAP
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES, GRANTABLE_SCOPES, RETIRED_SCOPES


def test_retired_scopes_are_out_of_the_catalogue() -> None:
    """Retired scopes are truly retired: no catalogue entry, default, or implication."""
    assert {"toolkits:read", "toolkits:write", "owner:toolkits:read"} == RETIRED_SCOPES
    assert not RETIRED_SCOPES & set(ALL_PERMISSIONS)
    assert not RETIRED_SCOPES & set(DEFAULT_AGENT_SCOPES)
    assert not RETIRED_SCOPES & GRANTABLE_SCOPES
    for implied in IMPLICATION_MAP.values():
        assert not RETIRED_SCOPES & implied


@pytest.mark.parametrize("scope", sorted(RETIRED_SCOPES))
def test_stored_scope_grant_resubmit_is_tolerated(scope: str) -> None:
    """A stored ``scope:grant`` carrying a retired scope re-validates without a 422.

    ``assert_grantable_scope`` guards both the file-time path
    (``AccessRequestService``) and the decide-time path (``EffectApplicator``);
    accepting here is what lets a pre-retirement request round-trip.
    """
    assert_grantable_scope(scope)  # must not raise


def test_unknown_scope_still_rejected() -> None:
    """Tolerance is scoped to the retired set — junk still 422s."""
    with pytest.raises(UnsupportedScopeGrantError):
        assert_grantable_scope("toolkits:banana")
