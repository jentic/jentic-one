"""Shared fixtures for broker unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture()
def load_material_spy() -> Iterator[AsyncMock]:
    """Stand in for ``CredentialRepository.load_material`` in resolver unit tests.

    Resolver unit tests patch ``list_by_vendor`` to return ``MagicMock``
    credentials whose material attributes are pre-set, so the material load is
    a no-op here; the spy lets tests assert which credentials it was asked for.
    The real query is covered by the broker integration tests.
    """
    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.load_material",
        new_callable=AsyncMock,
    ) as spy:
        yield spy
