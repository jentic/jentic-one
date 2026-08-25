"""Integration tests for bind-time toolkit resolution (M2, ``EffectsRepository``).

Exercises ``resolve_toolkits_for_api`` against a real control DB. Stored credential
identities are canonical (slugified vendor/name), so a *raw* reference axis
(``GitHub.com``) must be canonicalized at the SQL boundary or it would match
nothing — the regression these tests guard (S2).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_VENDOR_SLUG = "github-com"


@pytest.fixture()
async def clean_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR_SLUG))
            await session.execute(delete(Toolkit).where(Toolkit.name.like("tk-eff-%")))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_bound_credential(
    control_db: DatabaseSession,
    *,
    toolkit_name: str,
    api_name: str | None,
    api_version: str | None,
) -> str:
    """Seed a toolkit + canonical credential + binding; return the toolkit id."""
    toolkit = Toolkit(name=toolkit_name)
    credential = Credential(
        type="token_value",
        name=f"cred-{toolkit_name}",
        api_vendor=_VENDOR_SLUG,
        api_name=api_name,
        api_version=api_version,
    )
    async with control_db.session() as session:
        session.add(toolkit)
        session.add(credential)
        await session.flush()
        tk_id = toolkit.id
        session.add(
            ToolkitCredentialBinding(
                id=generate_ksuid("tcb"), toolkit_id=tk_id, credential_id=credential.id
            )
        )
        await session.commit()
    return tk_id


async def test_resolve_canonicalizes_raw_reference_vendor_and_name(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """A raw ``GitHub.com``/``Repos-API`` reference resolves the canonical stored row."""
    tk_id = await _seed_bound_credential(
        control_db, toolkit_name="tk-eff-canon", api_name="repos-api", api_version="v3"
    )

    async with control_db.session() as session:
        toolkits = await EffectsRepository.resolve_toolkits_for_api(
            session, vendor="GitHub.com", name="Repos-API", version="v3", owner_ids=None
        )

    assert toolkits == [tk_id]


async def test_resolve_wildcard_credential_matches_name_scoped_reference(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """A vendor-wide (NULL name) credential is resolved for a name-scoped reference."""
    tk_id = await _seed_bound_credential(
        control_db, toolkit_name="tk-eff-wild", api_name=None, api_version=None
    )

    async with control_db.session() as session:
        toolkits = await EffectsRepository.resolve_toolkits_for_api(
            session, vendor="github.com", name="repos-api", version="v3", owner_ids=None
        )

    assert toolkits == [tk_id]


async def test_resolve_prefers_exact_name_over_wildcard(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """An exact-name credential is preferred over a vendor-wide wildcard for the name."""
    exact_tk = await _seed_bound_credential(
        control_db, toolkit_name="tk-eff-exact", api_name="repos-api", api_version="v3"
    )
    await _seed_bound_credential(
        control_db, toolkit_name="tk-eff-any", api_name=None, api_version=None
    )

    async with control_db.session() as session:
        toolkits = await EffectsRepository.resolve_toolkits_for_api(
            session, vendor="github.com", name="repos-api", version="v3", owner_ids=None
        )

    assert toolkits == [exact_tk]
