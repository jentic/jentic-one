"""Branch coverage for the credential-create advisory registry probe (#1020).

The probe must be inert in every deployment shape it can meet: skipped when
the process is not granted the registry DB (standalone control), skipped when
the DB is allowed but was never connected (hand-rolled contexts, partial
startups — the gate is ``has_db``, not mere allowance), and swallowed when the
registry read itself fails. The failure case uses a *real* connected SQLite
with no ``apis`` table (no DB mocking — ``tests/arch/test_no_db_mocking.py``).
"""

from __future__ import annotations

from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.config import AppConfig, DatabaseConfig, DatabasesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models.api_identity import canonical_credential_scope


def _config() -> AppConfig:
    return AppConfig(
        databases=DatabasesConfig(
            registry=DatabaseConfig(backend="sqlite", path=":memory:"),
            admin=DatabaseConfig(backend="sqlite", path=":memory:"),
            control=DatabaseConfig(backend="sqlite", path=":memory:"),
        )
    )


_SCOPE = canonical_credential_scope(vendor="posthog-com", name="posthog-api", version="")


async def test_probe_skipped_when_registry_db_not_allowed() -> None:
    """Standalone control (registry not in allowed_dbs) skips without touching it."""
    ctx = Context(_config(), allowed_dbs={"control", "admin"})
    assert await CredentialService(ctx)._unmatched_scope_warnings(_SCOPE) == []


async def test_probe_skipped_when_registry_db_never_connected() -> None:
    """Allowed-but-unconnected (startup never ran) skips instead of raising on
    every create — the gate is ``has_db``, which stays False until connect."""
    ctx = Context(_config())
    assert await CredentialService(ctx)._unmatched_scope_warnings(_SCOPE) == []


async def test_probe_swallows_registry_read_errors() -> None:
    """A connected registry DB whose read fails (no ``apis`` table here)
    degrades to no warnings — the advisory must never fail the create."""
    ctx = Context(_config())
    await ctx.registry_db.connect()
    try:
        assert await CredentialService(ctx)._unmatched_scope_warnings(_SCOPE) == []
    finally:
        await ctx.registry_db.close()
