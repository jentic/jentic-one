"""Toolkit flattening's cross-owner review line (``cross_owner_binding``).

A derived pair is reported when the credential's creator is neither the
agent's owner nor the agent itself — and, conservatively, when either side
is unrecorded. The binding itself is never dropped; this only pins which
pairs get a review line.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jentic_one.control.services.toolkit_flattening import (
    CROSS_OWNER_BINDING_CATEGORY,
    Finding,
    ToolkitFlatteningService,
    _DerivedPair,
)

_AGENT = "agnt_1"
_OWNER = "usr_owner"
_CRED = "cred_1"


def _findings(
    *, owner: str | None, creator: str | None, credential_known: bool = True
) -> list[Any]:
    credentials = {_CRED: SimpleNamespace(created_by=creator)} if credential_known else {}
    snapshot: Any = SimpleNamespace(credentials=credentials, actor_owners={_AGENT: owner})
    findings: list[Finding] = []
    ToolkitFlatteningService._cross_owner_finding(
        _DerivedPair(agent_id=_AGENT, credential_id=_CRED), snapshot, findings
    )
    return findings


@pytest.mark.parametrize("creator", [_OWNER, _AGENT])
def test_same_owner_or_self_created_is_not_reported(creator: str) -> None:
    assert _findings(owner=_OWNER, creator=creator) == []


@pytest.mark.parametrize(
    ("owner", "creator", "credential_known"),
    [
        (_OWNER, "usr_other", True),  # another user's credential
        (None, "usr_other", True),  # agent owner unrecorded
        (_OWNER, None, True),  # credential creator unrecorded
        (None, None, True),  # both unrecorded — never "equal"
        (_OWNER, None, False),  # credential row missing
    ],
)
def test_cross_owner_or_unrecorded_side_is_reported(
    owner: str | None, creator: str | None, credential_known: bool
) -> None:
    (finding,) = _findings(owner=owner, creator=creator, credential_known=credential_known)
    assert finding.category == CROSS_OWNER_BINDING_CATEGORY
    assert finding.detail["agent_id"] == _AGENT
    assert finding.detail["agent_owner_id"] == owner
    assert finding.detail["credential_id"] == _CRED
    assert finding.detail["credential_created_by"] == creator
