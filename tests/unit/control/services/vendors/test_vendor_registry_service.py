"""Unit tests for the vendor registry service's DB-first / config-fallback seam.

These are pure-unit tests. No real database session is opened — the service
takes a ``VendorAppRegistrationSource`` in its constructor, so we inject a
tiny fake source that returns preconfigured ``OAuthAppRegistration`` values
(shaped like the ORM row, no ORM machinery required). Nothing here mocks
``AsyncSession``, ``DatabaseSession``, or ``sqlalchemy``.

The behaviour we pin down:

* ``get_entry`` prefers a DB row over a like-keyed config entry.
* ``get_entry`` falls back to the config entry when no DB row exists.
* ``get_entry`` raises when neither tier has the vendor.
* ``list_entries`` unions both sources, DB winning on collisions, and returns
  a stable display-name ordering.
* The config-only sync methods keep working unchanged.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from jentic_one.control.services.vendors.errors import UnknownVendorError
from jentic_one.control.services.vendors.schemas import VendorEntry
from jentic_one.control.services.vendors.service import (
    VendorAppRegistrationSource,
    VendorRegistryService,
)
from jentic_one.shared.config import (
    AppConfig,
    DatabaseConfig,
    DatabasesConfig,
    VendorAuthConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorIdentityProbeConfig,
    VendorRegistryConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context

# ---------------------------------------------------------------------------
# Fake registration source + fake OAuthAppRegistration
# ---------------------------------------------------------------------------


@dataclass
class _FakeDetails:
    """Duck-typed stand-in for either extension row.

    Auth-code flows read ``encrypted_client_secret`` + ``authorize_url`` +
    ``token_url``; device flow reads ``authorization_endpoint`` +
    ``token_endpoint``. All optional here so a single fake covers both
    branches — populate the fields relevant to the flow under test.
    """

    default_scopes: list[str] | None = None
    secret_last_rotated_at: dt.datetime | None = None
    encrypted_client_secret: str = ""
    authorize_url: str = "https://example.com/authorize"
    token_url: str = "https://example.com/token"
    authorization_endpoint: str = "https://example.com/device"
    token_endpoint: str = "https://example.com/device/token"


@dataclass
class _FakeRegistration:
    """Duck-typed stand-in for ``OAuthAppRegistration``.

    Only the fields the service reads through the synthesizer + projector
    are populated. Deliberately not an ORM object — using a dataclass keeps
    the tests decoupled from SQLAlchemy.
    """

    api_vendor: str
    name: str
    flow_kind: str
    client_id: str
    id: str = "oar_test"
    authorization_code_details: _FakeDetails | None = None
    device_authorization_details: _FakeDetails | None = None


class _FakeRegistrationSource:
    """Fake :class:`VendorAppRegistrationSource` returning preconfigured rows.

    The service does not import SQLAlchemy through this seam — the source
    interface takes plain domain args (``api_vendor``, ``flow_kind``), so the
    fake can implement it without ever touching an ``AsyncSession``.
    """

    def __init__(self, registrations: list[_FakeRegistration]) -> None:
        self._rows = registrations

    async def get_preferred_active(
        self, *, api_vendor: str, flow_kind: str | None = None
    ) -> _FakeRegistration | None:
        for row in self._rows:
            if row.api_vendor != api_vendor:
                continue
            if flow_kind is not None and row.flow_kind != flow_kind:
                continue
            return row
        return None

    async def list_active(self) -> list[_FakeRegistration]:
        return list(self._rows)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mk_config(vendor_entries: dict[str, VendorAuthConfig]) -> AppConfig:
    return AppConfig(
        databases=DatabasesConfig(
            registry=DatabaseConfig(backend="sqlite", path=":memory:"),
            admin=DatabaseConfig(backend="sqlite", path=":memory:"),
            control=DatabaseConfig(backend="sqlite", path=":memory:"),
        ),
        vendors=VendorRegistryConfig(entries=vendor_entries),
    )


def _github_config_entry() -> VendorAuthConfig:
    return VendorAuthConfig(
        vendor="github.com/api.github.com",
        display_name="GitHub (config)",
        flows=[
            VendorDeviceAuthorizationFlowConfig(
                client_id="config-cid",
                authorization_endpoint="https://github.com/login/device/code",
                token_endpoint="https://github.com/login/oauth/access_token",
            )
        ],
        scopes=[
            VendorScopeConfig(
                name="repo:read", classification="read", default=True, description=""
            ),
            VendorScopeConfig(
                name="repo:write", classification="write", default=False, description=""
            ),
        ],
        identity_probe=VendorIdentityProbeConfig(
            endpoint="https://api.github.com/user",
            identity_field="login",
            display_template="@{login}",
        ),
    )


def _slack_config_entry() -> VendorAuthConfig:
    return VendorAuthConfig(
        vendor="slack.com/api.slack.com",
        display_name="Slack (config)",
        flows=[
            VendorDeviceAuthorizationFlowConfig(
                client_id="slack-cid",
                authorization_endpoint="https://slack.com/oauth/device",
                token_endpoint="https://slack.com/api/oauth.v2.access",
            )
        ],
        identity_probe=VendorIdentityProbeConfig(
            endpoint="https://slack.com/api/auth.test",
            identity_field="user",
            display_template="@{user}",
        ),
    )


def _service(
    *,
    config_entries: dict[str, VendorAuthConfig] | None = None,
    registrations: list[_FakeRegistration] | None = None,
) -> VendorRegistryService:
    ctx = Context(_mk_config(config_entries or {}))
    source: VendorAppRegistrationSource = _FakeRegistrationSource(  # type: ignore[assignment]
        registrations or []
    )
    return VendorRegistryService(ctx, registration_source=source)


# ---------------------------------------------------------------------------
# get_entry: DB-first, config-fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_entry_prefers_db_over_like_keyed_config() -> None:
    """When both tiers know the slug, the DB row wins."""
    reg = _FakeRegistration(
        api_vendor="github",
        name="MyOrg GitHub App",
        flow_kind="device_authorization",
        client_id="db-cid",
        device_authorization_details=_FakeDetails(default_scopes=["repo"]),
    )
    svc = _service(
        config_entries={"github": _github_config_entry()},
        registrations=[reg],
    )

    entry = await svc.get_entry("github")

    assert entry.source == "db"
    # ``display_name`` is the vendor's family label (from the matching
    # config entry when one exists); ``name`` is the admin's registration
    # name — used as the picker card's primary label.
    assert entry.display_name == "GitHub (config)"
    assert entry.name == "MyOrg GitHub App"
    assert entry.client_id == "db-cid"
    assert entry.flow_kind == "device_authorization"
    assert entry.default_scopes == ["repo"]
    assert entry.entry_id == entry.registration_id
    assert entry.registration_id is not None


@pytest.mark.asyncio()
async def test_get_entry_falls_back_to_config_when_no_db_row() -> None:
    svc = _service(config_entries={"github": _github_config_entry()})

    entry = await svc.get_entry("github")

    assert entry.source == "config"
    assert entry.display_name == "GitHub (config)"
    assert entry.name == "GitHub (config)"
    assert entry.client_id == "config-cid"
    assert entry.flow_kind == "device_authorization"
    assert entry.entry_id == "github"
    assert entry.registration_id is None


@pytest.mark.asyncio()
async def test_get_entry_raises_when_neither_source_has_vendor() -> None:
    svc = _service()

    with pytest.raises(UnknownVendorError):
        await svc.get_entry("github")


@pytest.mark.asyncio()
async def test_get_entry_pins_flow_kind_when_supplied() -> None:
    """The flow_kind hint threads through to the repo's pin filter.

    A device-flow-only DB row must not shadow a config entry when the caller
    asks specifically for the auth-code flow.
    """
    device_row = _FakeRegistration(
        api_vendor="github",
        name="Device-only DB row",
        flow_kind="device_authorization",
        client_id="dev-cid",
        device_authorization_details=_FakeDetails(),
    )
    svc = _service(
        config_entries={"github": _github_config_entry()},
        registrations=[device_row],
    )

    # No pin — DB row wins.
    default = await svc.get_entry("github")
    assert default.source == "db"

    # Pin to auth-code — DB has no such row, falls back to config.
    pinned = await svc.get_entry("github", flow_kind="authorization_code")
    assert pinned.source == "config"


# ---------------------------------------------------------------------------
# list_entries: union with DB winning on collision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_list_entries_unions_db_and_config_with_db_winning() -> None:
    """A vendor present in both tiers surfaces once, DB row wins."""
    github_db = _FakeRegistration(
        api_vendor="github",
        name="GitHub (DB)",
        flow_kind="device_authorization",
        client_id="db-cid",
        device_authorization_details=_FakeDetails(),
    )
    svc = _service(
        config_entries={
            "github": _github_config_entry(),
            "slack": _slack_config_entry(),
        },
        registrations=[github_db],
    )

    entries = await svc.list_entries()

    # DB row's family display_name is the config vendor's, its ``name`` is
    # the admin registration name.
    github = next(e for e in entries if e.key == "github" and e.source == "db")
    assert github.name == "GitHub (DB)"
    assert github.display_name == "GitHub (config)"
    # Slack has no DB row — the config entry surfaces.
    assert any(e.key == "slack" and e.source == "config" for e in entries)
    # DB replaces config: no config-sourced github entry.
    assert not any(e.key == "github" and e.source == "config" for e in entries)


@pytest.mark.asyncio()
async def test_list_entries_surfaces_db_only_vendor() -> None:
    """An admin-registered vendor with no like-keyed config entry still lists."""
    only_db = _FakeRegistration(
        api_vendor="notion",
        name="Notion (admin)",
        flow_kind="authorization_code",
        client_id="notion-cid",
        authorization_code_details=_FakeDetails(default_scopes=["read"]),
    )
    svc = _service(registrations=[only_db])

    entries = await svc.list_entries()

    assert [e.key for e in entries] == ["notion"]
    assert entries[0].source == "db"
    assert entries[0].has_client_secret is True


@pytest.mark.asyncio()
async def test_list_entries_stable_display_name_ordering() -> None:
    """Sort key is display_name — the picker must not shuffle between polls."""
    a_db = _FakeRegistration(
        api_vendor="zeta",
        name="Alpha (DB)",
        flow_kind="device_authorization",
        client_id="a",
        device_authorization_details=_FakeDetails(),
    )
    z_db = _FakeRegistration(
        api_vendor="alpha",
        name="Zeta (DB)",
        flow_kind="device_authorization",
        client_id="z",
        device_authorization_details=_FakeDetails(),
    )
    svc = _service(registrations=[a_db, z_db])

    entries = await svc.list_entries()

    assert [e.display_name for e in entries] == ["Alpha (DB)", "Zeta (DB)"]


@pytest.mark.asyncio()
async def test_list_entries_empty_when_neither_source_has_vendors() -> None:
    svc = _service()
    assert await svc.list_entries() == []


@pytest.mark.asyncio()
async def test_list_entries_yields_one_entry_per_registration_for_same_vendor() -> None:
    """Two admin-registered OAuth apps for the same ``api_vendor`` surface
    as two picker rows — earlier dedupe-by-vendor was the bug that made the
    second registration invisible to users.
    """
    prod = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_prod",
        name="MyOrg Prod Gmail",
        flow_kind="authorization_code",
        client_id="prod-cid",
        authorization_code_details=_FakeDetails(default_scopes=["mail.readonly"]),
    )
    sandbox = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_sandbox",
        name="MyOrg Sandbox Gmail",
        flow_kind="authorization_code",
        client_id="sandbox-cid",
        authorization_code_details=_FakeDetails(default_scopes=["mail.readonly"]),
    )
    svc = _service(registrations=[prod, sandbox])

    entries = await svc.list_entries()

    # Two rows, both keyed by the same vendor slug but with distinct entry
    # ids and admin-picked names.
    gmails = [e for e in entries if e.key == "googleapis-com"]
    assert len(gmails) == 2
    ids = {e.entry_id for e in gmails}
    assert ids == {"oar_prod", "oar_sandbox"}
    names = {e.name for e in gmails}
    assert names == {"MyOrg Prod Gmail", "MyOrg Sandbox Gmail"}
    # Both entries carry the registration id, both are source=db.
    assert all(e.registration_id is not None and e.source == "db" for e in gmails)


# ---------------------------------------------------------------------------
# get / list_all: async, DB-first + config-fallback, VendorAuthConfig shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_returns_config_when_no_db_row() -> None:
    """The config entry surfaces as ``VendorAuthConfig`` unchanged."""
    svc = _service(config_entries={"github": _github_config_entry()})
    entry = await svc.get("github")
    assert isinstance(entry, VendorAuthConfig)
    assert entry.display_name == "GitHub (config)"


@pytest.mark.asyncio()
async def test_list_all_unions_config_and_db_with_db_winning() -> None:
    github_db = _FakeRegistration(
        api_vendor="github",
        name="GitHub (DB)",
        flow_kind="device_authorization",
        client_id="db-cid",
        device_authorization_details=_FakeDetails(),
    )
    svc = _service(
        config_entries={
            "github": _github_config_entry(),
            "slack": _slack_config_entry(),
        },
        registrations=[github_db],
    )
    entries = await svc.list_all()
    display_names = {e.display_name for e in entries}
    assert display_names == {"GitHub (DB)", "Slack (config)"}


@pytest.mark.asyncio()
async def test_get_raises_unknown_vendor_when_missing() -> None:
    svc = _service()
    with pytest.raises(UnknownVendorError):
        await svc.get("github")


@pytest.mark.asyncio()
async def test_get_merges_db_flow_with_config_metadata() -> None:
    """When both tiers know the vendor, DB supplies the flow, config the metadata.

    ``identity_probe`` + ``scopes`` + canonical ``vendor`` come off the
    config side; ``display_name`` and ``client_id`` come off the DB row.
    """
    github_db = _FakeRegistration(
        api_vendor="github",
        name="MyOrg GitHub App",
        flow_kind="device_authorization",
        client_id="db-cid",
        device_authorization_details=_FakeDetails(default_scopes=["repo"]),
    )
    svc = _service(
        config_entries={"github": _github_config_entry()},
        registrations=[github_db],
    )
    entry = await svc.get("github")
    assert entry.display_name == "MyOrg GitHub App"
    assert entry.flows[0].client_id == "db-cid"
    assert entry.vendor == "github.com/api.github.com"
    assert entry.identity_probe is not None
    assert entry.identity_probe.endpoint == "https://api.github.com/user"
    assert [s.name for s in entry.scopes] == ["repo:read", "repo:write"]


@pytest.mark.asyncio()
async def test_get_synthesizes_when_no_matching_config() -> None:
    """DB-only vendor synthesizes a minimal ``VendorAuthConfig``.

    Identity-probe is a placeholder — the connect flow will fail cleanly at
    identity-echo until an operator adds a matching config entry.

    Uses device flow because it's a public client (no secret to decrypt) —
    the auth-code equivalent needs a real ``ctx.encryption`` fixture to
    round-trip the encrypted secret, which is exercised by the integration
    tests instead.
    """
    only_db = _FakeRegistration(
        api_vendor="notion",
        name="Notion (admin)",
        flow_kind="device_authorization",
        client_id="notion-cid",
        device_authorization_details=_FakeDetails(default_scopes=["read"]),
    )
    svc = _service(registrations=[only_db])
    entry = await svc.get("notion")
    assert entry.display_name == "Notion (admin)"
    assert entry.vendor == "notion/notion"
    assert entry.flows[0].client_id == "notion-cid"
    # No matching config → identity_probe is None. Callers must skip the
    # identity-echo step; the credential still stores with connected_as=None.
    assert entry.identity_probe is None


# ---------------------------------------------------------------------------
# Config-source projection edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_config_projection_carries_default_scopes_when_marked() -> None:
    """VendorEntry.default_scopes for config = scopes flagged ``default=True``."""
    svc = _service(config_entries={"github": _github_config_entry()})
    entry = await svc.get_entry("github")
    assert entry.default_scopes == ["repo:read"]


@pytest.mark.asyncio()
async def test_config_projection_omits_scopes_when_none_default() -> None:
    """Slack config carries no scopes at all → default_scopes is None."""
    svc = _service(config_entries={"slack": _slack_config_entry()})
    entry = await svc.get_entry("slack")
    assert entry.default_scopes is None


@pytest.mark.asyncio()
async def test_projected_entry_is_a_pydantic_model() -> None:
    """The unified view is a Pydantic model so callers get schema validation."""
    svc = _service(config_entries={"github": _github_config_entry()})
    entry = await svc.get_entry("github")
    assert isinstance(entry, VendorEntry)
