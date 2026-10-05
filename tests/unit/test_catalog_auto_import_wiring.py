"""``InProcessCatalogAutoImporter`` actor attribution (theme-8 Phase 4).

A connect session that pre-dates the service-account retirement can still
carry an ``sva_`` initiator id. The auto-import must not relabel it as a user
(that would forge the audit trail) — it skips with an info log instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from jentic_one.shared.models.actors import ActorType
from jentic_one.wiring import InProcessCatalogAutoImporter


def _catalog_service() -> MagicMock:
    svc = MagicMock()
    svc.get = AsyncMock(return_value=MagicMock(registered=False))
    svc.import_entry = AsyncMock(return_value="job_1")
    return svc


@pytest.mark.parametrize(
    ("initiator", "actor_type"),
    [("usr_1", ActorType.USER), ("agnt_1", ActorType.AGENT)],
)
async def test_auto_import_attributes_live_actor(initiator: str, actor_type: ActorType) -> None:
    svc = _catalog_service()
    with patch("jentic_one.wiring.CatalogService", return_value=svc):
        job_id = await InProcessCatalogAutoImporter(MagicMock()).ensure_imported(
            api_id="stripe.com", initiator_actor_id=initiator
        )
    assert job_id == "job_1"
    assert svc.import_entry.await_args is not None
    identity = svc.import_entry.await_args.args[1]
    assert identity.sub == initiator
    assert identity.actor_type == actor_type


async def test_auto_import_skips_retired_service_account_initiator() -> None:
    svc = _catalog_service()
    with (
        patch("jentic_one.wiring.CatalogService", return_value=svc),
        structlog.testing.capture_logs() as logs,
    ):
        job_id = await InProcessCatalogAutoImporter(MagicMock()).ensure_imported(
            api_id="stripe.com", initiator_actor_id="sva_legacy"
        )
    assert job_id is None
    svc.import_entry.assert_not_awaited()
    skipped = [e for e in logs if e["event"] == "catalog_auto_import.skipped_retired_initiator"]
    assert len(skipped) == 1
    assert skipped[0]["log_level"] == "info"
    assert skipped[0]["initiator"] == "sva_legacy"
