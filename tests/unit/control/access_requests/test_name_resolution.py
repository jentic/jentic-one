"""Unit tests for access-request service name resolution."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import MagicMock

from jentic_one.control.services.access_requests.schemas.access_requests import ResolvedNames
from jentic_one.control.services.access_requests.service import AccessRequestService


def _make_item(**overrides: Any) -> MagicMock:
    defaults = {
        "id": "arqi_001",
        "resource_type": "credential",
        "action": "read",
        "resource_id": "cred_abc",
        "resource_reference": None,
        "to_type": "toolkit",
        "to_id": "tk_xyz",
        "rules": None,
        "rule_set_id": None,
        "status": "pending",
        "applied_effects": None,
        "decided_by": None,
        "decided_at": None,
        "decision_reason": None,
    }
    defaults.update(overrides)
    item = MagicMock()
    for k, v in defaults.items():
        setattr(item, k, v)
    return item


def _make_request(items: list[Any] | None = None, **overrides: Any) -> MagicMock:
    defaults = {
        "id": "areq_001",
        "actor_id": "agt_test",
        "reason": "Need access",
        "requested_by": "usr_filer",
        "status": "pending",
        "approve_url": "https://example.com/access-requests/areq_001",
        "filed_at": dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        "expires_at": dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        "created_by": "usr_filer",
        "filer_owner_id": "usr_filer",
        "items": items or [_make_item()],
    }
    defaults.update(overrides)
    request = MagicMock()
    for k, v in defaults.items():
        setattr(request, k, v)
    return request


def test_collect_resource_ids_toolkit_resource() -> None:
    item = _make_item(resource_type="toolkit", resource_id="tk_direct", to_type=None, to_id=None)
    request = _make_request(items=[item])
    collected = AccessRequestService._collect_resource_ids(request)
    assert "tk_direct" in collected.toolkit_ids
    assert collected.credential_ids == []


def test_collect_resource_ids_credential_resource() -> None:
    item = _make_item(resource_type="credential", resource_id="cred_abc", to_type=None, to_id=None)
    request = _make_request(items=[item])
    collected = AccessRequestService._collect_resource_ids(request)
    assert collected.toolkit_ids == []
    assert "cred_abc" in collected.credential_ids


def test_collect_resource_ids_toolkit_as_to_target() -> None:
    item = _make_item(
        resource_type="credential", resource_id="cred_abc", to_type="toolkit", to_id="tk_target"
    )
    request = _make_request(items=[item])
    collected = AccessRequestService._collect_resource_ids(request)
    assert "tk_target" in collected.toolkit_ids
    assert "cred_abc" in collected.credential_ids


def test_to_view_populates_toolkit_name_for_toolkit_resource() -> None:
    item = _make_item(resource_type="toolkit", resource_id="tk_abc", to_type=None, to_id=None)
    request = _make_request(items=[item])
    ctx = MagicMock()
    svc = AccessRequestService(ctx)
    names = ResolvedNames(toolkit_names={"tk_abc": "Toolkit Alpha"})
    view = svc._to_view(request, names=names)
    assert view.items[0].toolkit_name == "Toolkit Alpha"
    assert view.items[0].credential_name is None


def test_to_view_populates_credential_name_for_credential_resource() -> None:
    item = _make_item(
        resource_type="credential", resource_id="cred_xyz", to_type="toolkit", to_id="tk_parent"
    )
    request = _make_request(items=[item])
    ctx = MagicMock()
    svc = AccessRequestService(ctx)
    names = ResolvedNames(
        toolkit_names={"tk_parent": "Parent Toolkit"},
        credential_names={"cred_xyz": "My Credential"},
    )
    view = svc._to_view(request, names=names)
    assert view.items[0].credential_name == "My Credential"
    assert view.items[0].toolkit_name == "Parent Toolkit"


def test_to_view_names_none_when_ids_missing() -> None:
    item = _make_item(resource_type="toolkit", resource_id="tk_missing", to_type=None, to_id=None)
    request = _make_request(items=[item])
    ctx = MagicMock()
    svc = AccessRequestService(ctx)
    view = svc._to_view(request, names=ResolvedNames())
    assert view.items[0].toolkit_name is None
    assert view.items[0].credential_name is None


def test_to_view_names_none_when_no_maps_provided() -> None:
    item = _make_item(resource_type="toolkit", resource_id="tk_abc", to_type=None, to_id=None)
    request = _make_request(items=[item])
    ctx = MagicMock()
    svc = AccessRequestService(ctx)
    view = svc._to_view(request)
    assert view.items[0].toolkit_name is None
    assert view.items[0].credential_name is None
