"""Unit tests for shared lookup helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jentic_one.shared.lookups import resolve_credential_names


@pytest.mark.asyncio
async def test_resolve_credential_names_empty_input() -> None:
    """Empty ID list returns empty dict without hitting the database."""
    session = AsyncMock()
    result = await resolve_credential_names(session, [])
    assert result == {}
    session.execute.assert_not_called()
