"""Enforcement tests for least-privilege gating on registry read routes.

Two complementary directions:

* An under-scoped caller (holds an unrelated scope) is **denied** 403 on
  ``GET /apis``, proving the gate is real and not a no-op.
* A caller holding only ``apis:write`` is **admitted** to ``GET /apis`` — the
  route guard expands implications (``apis:write`` ⇒ ``apis:read``) so the
  advertised catalogue semantics hold at enforcement, not just in the docs.

The catalog import route (``POST /catalog/{api_id}:import``) is gated on the
narrow ``catalog:import`` scope: a ``catalog:import``-only caller is admitted
there but denied on ``POST /apis``, and an ``apis:write`` caller reaches it via
the ``apis:write`` ⇒ ``catalog:import`` implication.

The overlay confirm route (``POST /apis/{v}/{n}/{ver}/overlays/{id}:confirm``)
is gated on the narrow, operator-only ``overlays:confirm`` scope: a
``overlays:confirm``-only caller is admitted there (auth passes; a missing
overlay surfaces later as 4xx, not 403), an ``apis:write`` caller — who can
*submit* overlays — is **denied** because ``apis:write`` deliberately does NOT
imply ``overlays:confirm``, and an ``org:admin`` caller is admitted via the
short-circuit / implication.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_CONFIRM_PATH = "/apis/nonexistent.test/api/v1/overlays/ovl_nonexistent:confirm"


def test_list_apis_denied_without_scope(wrong_scope_client: TestClient) -> None:
    assert wrong_scope_client.get("/apis").status_code == 403


def test_write_scope_implies_read_on_list_apis(write_only_client: TestClient) -> None:
    # apis:write implies apis:read — the guard must expand it, so this is 200 not 403.
    assert write_only_client.get("/apis").status_code == 200


def test_catalog_import_scope_admits_catalog_import(
    catalog_import_only_client: TestClient,
) -> None:
    # catalog:import gates the catalog import route — the guard must admit the
    # caller (not 403). A missing catalog entry surfaces later as 4xx/5xx, not
    # as an authorization failure.
    resp = catalog_import_only_client.post("/catalog/nonexistent:import")
    assert resp.status_code != 403


def test_catalog_import_scope_denied_on_apis_write(
    catalog_import_only_client: TestClient,
) -> None:
    # catalog:import does NOT imply apis:write — the generic import stays gated.
    assert catalog_import_only_client.post("/apis", json={}).status_code == 403


def test_write_scope_admits_catalog_import(write_only_client: TestClient) -> None:
    # apis:write ⇒ catalog:import — the guard expands the implication, so the
    # caller reaches the catalog import route (not 403).
    resp = write_only_client.post("/catalog/nonexistent:import")
    assert resp.status_code != 403


def test_overlays_confirm_scope_admits_confirm(
    overlays_confirm_only_client: TestClient,
) -> None:
    # overlays:confirm gates the confirm route — the guard admits the caller (not
    # 403). A missing API/overlay surfaces later as 4xx, not as an auth failure.
    resp = overlays_confirm_only_client.post(_CONFIRM_PATH, json={})
    assert resp.status_code != 403


def test_apis_write_denied_on_overlay_confirm(write_only_client: TestClient) -> None:
    # Security-relevant non-implication: apis:write lets a contributor SUBMIT overlays
    # but must NOT reach confirm (which rewrites the served spec). apis:write does not
    # imply overlays:confirm, so the guard denies with 403.
    resp = write_only_client.post(_CONFIRM_PATH, json={})
    assert resp.status_code == 403


def test_wrong_scope_denied_on_overlay_confirm(wrong_scope_client: TestClient) -> None:
    # An unrelated scope (events:read) is denied — proves the gate is real.
    resp = wrong_scope_client.post(_CONFIRM_PATH, json={})
    assert resp.status_code == 403


def test_org_admin_admitted_on_overlay_confirm(admin_client: TestClient) -> None:
    # org:admin implies overlays:confirm (and short-circuits), so it is admitted.
    resp = admin_client.post(_CONFIRM_PATH, json={})
    assert resp.status_code != 403
