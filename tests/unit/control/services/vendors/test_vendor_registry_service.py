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

from jentic_one.control.services.integrations.errors import (
    InvalidOAuthAppRegistrationError,
)
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
    # Post-refactor: registrations are self-describing. Tests default to
    # populated values so the projection uses the real catalog id / family
    # label; a specific test sets ``catalog_api_id=None`` to exercise the
    # pre-refactor degraded state warning.
    catalog_api_id: str | None = "example.com/api.example.com"
    display_name: str | None = "Example (DB)"
    authorization_code_details: _FakeDetails | None = None
    device_authorization_details: _FakeDetails | None = None
    is_active: bool = True


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

    async def get_by_id(self, registration_id: str) -> _FakeRegistration | None:
        for row in self._rows:
            if row.id == registration_id:
                return row
        return None


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
    """When both tiers know the slug, the DB row wins — but no config merge.

    The DB row's ``display_name`` is the admin-supplied one, not the config
    entry's. The two tiers are peers on ``list_entries`` (both surface as
    separate cards) but ``get_entry`` still prefers the DB row when the
    caller asks for a single vendor slug.
    """
    reg = _FakeRegistration(
        api_vendor="github",
        name="MyOrg GitHub App",
        flow_kind="device_authorization",
        client_id="db-cid",
        display_name="MyOrg GitHub (family)",
        device_authorization_details=_FakeDetails(default_scopes=["repo"]),
    )
    svc = _service(
        config_entries={"github": _github_config_entry()},
        registrations=[reg],
    )

    entry = await svc.get_entry("github")

    assert entry.source == "db"
    # ``display_name`` is the admin-supplied family label from the
    # registration itself — the config entry's ``display_name`` never
    # bleeds in.
    assert entry.display_name == "MyOrg GitHub (family)"
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
async def test_list_entries_unions_db_and_config_as_peers() -> None:
    """A vendor present in both tiers surfaces as **two** entries, no merge.

    Post-decouple: admin registrations and platform config entries are
    fully-independent parallel entrypoints. A GitHub DB registration does
    NOT hide the shipped GitHub config entry — users see both cards and
    pick whichever one they want to SSO through.
    """
    github_db = _FakeRegistration(
        api_vendor="github",
        name="GitHub (DB)",
        display_name="GitHub (DB family)",
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

    # DB and config BOTH surface for github.
    github_db_entry = next(e for e in entries if e.key == "github" and e.source == "db")
    github_config_entry = next(e for e in entries if e.key == "github" and e.source == "config")
    assert github_db_entry.name == "GitHub (DB)"
    # DB row's family label is its own — no merge with the config entry.
    assert github_db_entry.display_name == "GitHub (DB family)"
    assert github_config_entry.display_name == "GitHub (config)"
    # Slack has no DB row — the config entry surfaces alone.
    assert any(e.key == "slack" and e.source == "config" for e in entries)


@pytest.mark.asyncio()
async def test_list_entries_surfaces_db_only_vendor() -> None:
    """An admin-registered vendor with no like-keyed config entry still lists."""
    only_db = _FakeRegistration(
        api_vendor="notion",
        name="Notion (admin)",
        display_name="Notion",
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
        display_name="Alpha (DB)",
        flow_kind="device_authorization",
        client_id="a",
        device_authorization_details=_FakeDetails(),
    )
    z_db = _FakeRegistration(
        api_vendor="alpha",
        name="Zeta (DB)",
        display_name="Zeta (DB)",
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
async def test_list_all_yields_registrations_and_config_side_by_side() -> None:
    """Registrations and config entries are peers — a vendor present in both
    tiers surfaces as two separate entries. No dedupe by vendor slug.
    """
    github_db = _FakeRegistration(
        api_vendor="github",
        name="GitHub (DB)",
        display_name="GitHub (DB)",
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
    # Both GitHub entries (DB + config) surface alongside Slack (config only).
    assert display_names == {"GitHub (DB)", "GitHub (config)", "Slack (config)"}


@pytest.mark.asyncio()
async def test_get_raises_unknown_vendor_when_missing() -> None:
    svc = _service()
    with pytest.raises(UnknownVendorError):
        await svc.get("github")


@pytest.mark.asyncio()
async def test_get_reads_db_only_no_config_merge() -> None:
    """Admin registrations project standalone — the config entry is never
    consulted, even when a matching-slug one exists.

    ``vendor`` on the returned ``VendorAuthConfig`` is the admin-picked
    ``catalog_api_id``. ``display_name`` is the admin-supplied family
    label. ``identity_probe`` is always ``None`` for DB rows — that's a
    platform-config concern.
    """
    github_db = _FakeRegistration(
        api_vendor="github",
        name="MyOrg GitHub App",
        display_name="MyOrg GitHub",
        catalog_api_id="github.com/api.github.com",
        flow_kind="device_authorization",
        client_id="db-cid",
        device_authorization_details=_FakeDetails(default_scopes=["repo"]),
    )
    svc = _service(
        config_entries={"github": _github_config_entry()},
        registrations=[github_db],
    )
    entry = await svc.get("github")
    # DB registration is standalone; nothing bleeds in from the config.
    assert entry.display_name == "MyOrg GitHub"
    assert entry.flows[0].client_id == "db-cid"
    assert entry.vendor == "github.com/api.github.com"
    # No config merge → no identity probe, no config scope catalog.
    assert entry.identity_probe is None
    assert [s.name for s in entry.scopes] == ["repo"]


@pytest.mark.asyncio()
async def test_get_projects_db_only_with_catalog_api_id() -> None:
    """A DB-only vendor projects using its ``catalog_api_id`` as the ``vendor``
    string on the returned ``VendorAuthConfig`` — this feeds
    ``credential.catalog_api_id`` at connect time so the operations preview
    resolves against a real registered API.

    Uses device flow (public client, no secret to decrypt).
    """
    only_db = _FakeRegistration(
        api_vendor="notion",
        name="Notion (admin)",
        display_name="Notion",
        catalog_api_id="notion.com/api.notion.com",
        flow_kind="device_authorization",
        client_id="notion-cid",
        device_authorization_details=_FakeDetails(default_scopes=["read"]),
    )
    svc = _service(registrations=[only_db])
    entry = await svc.get("notion")
    assert entry.display_name == "Notion"
    # ``vendor`` on the projected config is the admin-picked catalog API id.
    # This is the value stamped onto ``credential.catalog_api_id`` at connect
    # time, so the operations preview resolves against a real registered API.
    assert entry.vendor == "notion.com/api.notion.com"
    assert entry.flows[0].client_id == "notion-cid"
    # Admin registrations never carry an identity probe — that's a
    # platform-config concern. Credentials land with connected_as=None.
    assert entry.identity_probe is None


@pytest.mark.asyncio()
async def test_get_falls_back_to_placeholder_vendor_for_pre_refactor_row() -> None:
    """Pre-refactor registrations without ``catalog_api_id`` still project —
    the ``vendor`` string falls back to a ``<slug>/<slug>`` placeholder and
    the service logs a warning. The connect flow still runs; only the
    operations preview degrades until an admin picks a real catalog API.
    """
    legacy = _FakeRegistration(
        api_vendor="notion",
        name="Notion (pre-refactor)",
        display_name=None,
        catalog_api_id=None,
        flow_kind="device_authorization",
        client_id="notion-cid",
        device_authorization_details=_FakeDetails(),
    )
    svc = _service(registrations=[legacy])
    entry = await svc.get("notion")
    assert entry.vendor == "notion/notion"
    # ``display_name`` falls back to the registration's ``name`` when the
    # admin never supplied a family label.
    assert entry.display_name == "Notion (pre-refactor)"


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


# ---------------------------------------------------------------------------
# resolve_by_pin: the single seam every vendor read routes through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_resolve_by_pin_returns_pinned_registration_over_preferred() -> None:
    """Two Gmail registrations with different default_scopes — pinning the
    non-preferred one returns *its* scopes, not the preferred-active one's.

    Uses device-flow registrations so the synthesizer doesn't hit encryption
    (auth-code decrypts ``encrypted_client_secret`` through ``ctx.encryption``,
    which isn't configured in these pure-unit tests). The scope-per-pin
    invariant is flow-kind-agnostic.
    """
    prod = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_prod",
        name="MyOrg Prod Gmail",
        flow_kind="device_authorization",
        client_id="prod-cid",
        device_authorization_details=_FakeDetails(default_scopes=["mail.readonly"]),
    )
    sandbox = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_sandbox",
        name="MyOrg Sandbox Gmail",
        flow_kind="device_authorization",
        client_id="sandbox-cid",
        device_authorization_details=_FakeDetails(default_scopes=["mail.send", "mail.compose"]),
    )
    svc = _service(registrations=[prod, sandbox])

    # Pinning sandbox returns sandbox's scopes + client_id, regardless of
    # which one ``get_preferred_active`` would have returned.
    entry = await svc.resolve_by_pin("googleapis-com", registration_id="oar_sandbox")
    scope_names = {s.name for s in entry.scopes}
    assert scope_names == {"mail.send", "mail.compose"}
    assert entry.flows[0].client_id == "sandbox-cid"


@pytest.mark.asyncio()
async def test_resolve_by_pin_raises_on_missing_registration() -> None:
    svc = _service(config_entries={"github": _github_config_entry()})
    with pytest.raises(InvalidOAuthAppRegistrationError) as excinfo:
        await svc.resolve_by_pin("github", registration_id="oar_nope")
    assert "not found" in str(excinfo.value)


@pytest.mark.asyncio()
async def test_resolve_by_pin_raises_on_vendor_mismatch() -> None:
    """A pin whose ``api_vendor`` doesn't match the requested vendor slug is
    refused — silent fall-through would mint credentials against the wrong
    vendor's OAuth app.
    """
    gmail = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_gmail",
        name="MyOrg Gmail",
        flow_kind="device_authorization",
        client_id="gmail-cid",
        device_authorization_details=_FakeDetails(default_scopes=["mail.readonly"]),
    )
    svc = _service(registrations=[gmail])
    with pytest.raises(InvalidOAuthAppRegistrationError) as excinfo:
        # Ask for GitHub but hand over the Gmail pin.
        await svc.resolve_by_pin("github", registration_id="oar_gmail")
    assert "api_vendor mismatch" in str(excinfo.value)


@pytest.mark.asyncio()
async def test_resolve_by_pin_raises_on_inactive_registration() -> None:
    inactive = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_inactive",
        name="MyOrg Gmail (retired)",
        flow_kind="device_authorization",
        client_id="cid",
        device_authorization_details=_FakeDetails(default_scopes=["mail.readonly"]),
        is_active=False,
    )
    svc = _service(registrations=[inactive])
    with pytest.raises(InvalidOAuthAppRegistrationError) as excinfo:
        await svc.resolve_by_pin("googleapis-com", registration_id="oar_inactive")
    assert "inactive" in str(excinfo.value)


@pytest.mark.asyncio()
async def test_validate_scopes_honours_pin_when_registrations_differ() -> None:
    """The user picked sandbox (scopes: send, compose); a request confirming
    the *preferred* registration's scope (readonly) is rejected as unknown."""
    prod = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_prod",
        name="MyOrg Prod Gmail",
        flow_kind="device_authorization",
        client_id="prod-cid",
        device_authorization_details=_FakeDetails(default_scopes=["mail.readonly"]),
    )
    sandbox = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_sandbox",
        name="MyOrg Sandbox Gmail",
        flow_kind="device_authorization",
        client_id="sandbox-cid",
        device_authorization_details=_FakeDetails(default_scopes=["mail.send", "mail.compose"]),
    )
    svc = _service(registrations=[prod, sandbox])

    unknown = await svc.validate_scopes(
        "googleapis-com",
        ["mail.readonly"],
        registration_id="oar_sandbox",
    )
    assert unknown == ["mail.readonly"]

    # And the sandbox-native scope validates cleanly under the same pin.
    unknown = await svc.validate_scopes(
        "googleapis-com",
        ["mail.send"],
        registration_id="oar_sandbox",
    )
    assert unknown == []
