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
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


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
