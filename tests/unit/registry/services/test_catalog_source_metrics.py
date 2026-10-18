"""Catalog-source sync metrics: the failed-run counter names why a run failed."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from jentic_one.registry.services.catalog.sources import metrics


@pytest.fixture()
def counter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setitem(metrics._counters, "catalog_source_sync_total", fake)
    monkeypatch.setitem(metrics._histograms, "duration", MagicMock())
    return fake


def _attributes(counter: MagicMock) -> list[dict[str, Any]]:
    return [call.args[1] for call in counter.add.call_args_list]


def test_sync_total_carries_the_error_code_of_a_failed_run(counter: MagicMock) -> None:
    metrics.record_sync("corp", "failed", 0.2, "listing_too_large")
    metrics.record_sync("corp", "applied", 0.1)
    assert _attributes(counter) == [
        {"source": "corp", "status": "failed", "error_code": "listing_too_large"},
        {"source": "corp", "status": "applied"},
    ]
