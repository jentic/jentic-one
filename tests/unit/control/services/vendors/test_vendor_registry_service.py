"""Unit tests for the vendor registry service's DB-first / config-fallback seam.

These are pure-unit tests. No real database session is opened — the service
takes a ``VendorAppRegistrationSource`` in its constructor, so we inject a
tiny fake source that returns preconfigured ``OAuthAppRegistration`` values
(shaped like the ORM row, no ORM machinery required). Nothing here mocks
``AsyncSession``, ``DatabaseSession``, or ``sqlalchemy``.

The behaviour we pin down:

* ``resolve_connect_source`` with no pin: config entry first, else the single
  active registration for the slug + flow, else ambiguous / unknown.
* ``resolve_session_source`` re-opens the session's own source and never
  re-prefers a registration added since.
* ``list_entries`` / ``list_all`` surface both tiers as peers, stably ordered.
* Entry and flow always come from the same source.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from jentic_one.control.services.integrations.errors import (
    InvalidOAuthAppRegistrationError,
)
from jentic_one.control.services.vendors.errors import (
    AmbiguousVendorError,
    UnknownVendorError,
    UnsupportedFlowError,
)
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
    VendorAuthorizationCodeFlowConfig,
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
    # Registrations are self-describing: the catalog API they target and
    # the vendor family label.
    catalog_api_id: str = "example.com/api.example.com"
    display_name: str = "Example (DB)"
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

    async def list_active_for_vendor(
        self, *, api_vendor: str, flow_kind: str | None = None
    ) -> list[_FakeRegistration]:
        return [
            row
            for row in self._rows
            if row.is_active
            and row.api_vendor == api_vendor
            and (flow_kind is None or row.flow_kind == flow_kind)
        ]

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
# resolve_connect_source: the no-pin rule at ``:connect``
# ---------------------------------------------------------------------------


def _gmail(reg_id: str, flow_kind: str = "device_authorization") -> _FakeRegistration:
    details = _FakeDetails(default_scopes=[f"{reg_id}.scope"])
    return _FakeRegistration(
        api_vendor="googleapis-com",
        id=reg_id,
        name=f"Google {reg_id}",
        flow_kind=flow_kind,
        client_id=f"{reg_id}-cid",
        catalog_api_id=f"googleapis.com/{reg_id}",
        authorization_code_details=details if flow_kind == "authorization_code" else None,
        device_authorization_details=details if flow_kind == "device_authorization" else None,
    )


@pytest.mark.asyncio()
async def test_connect_no_pin_prefers_config_over_like_keyed_registration() -> None:
    """Config picker cards send no pin, so a same-slug registration must not
    take them over — the config entry is the no-pin source when it exists."""
    reg = _FakeRegistration(
        api_vendor="github",
        name="MyOrg GitHub App",
        flow_kind="device_authorization",
        client_id="db-cid",
        device_authorization_details=_FakeDetails(default_scopes=["repo"]),
    )
    svc = _service(config_entries={"github": _github_config_entry()}, registrations=[reg])

    resolved = await svc.resolve_connect_source("github")

    assert resolved.source == "config"
    assert resolved.registration is None
    assert resolved.entry.display_name == "GitHub (config)"
    assert resolved.flow.client_id == "config-cid"


@pytest.mark.asyncio()
async def test_connect_no_pin_uses_registration_when_config_lacks_flow() -> None:
    """A config entry without the preferred flow falls through to a
    registration that offers it."""
    reg = _gmail("oar_ac", flow_kind="authorization_code")
    reg.api_vendor = "github"
    svc = _service(config_entries={"github": _github_config_entry()}, registrations=[reg])

    resolved = await svc.resolve_connect_source("github", preferred_flow="authorization_code")

    assert resolved.registration is not None
    assert resolved.registration.id == reg.id
    assert resolved.flow.kind == "authorization_code"


@pytest.mark.asyncio()
async def test_connect_no_pin_single_registration_supplies_entry_and_flow() -> None:
    """Entry (scopes, catalog id) and flow (client id) come from the same app."""
    reg = _gmail("oar_calendar")
    svc = _service(registrations=[reg])

    resolved = await svc.resolve_connect_source("googleapis-com")

    assert resolved.registration is not None
    assert resolved.registration.id == reg.id
    assert resolved.entry.vendor == "googleapis.com/oar_calendar"
    assert [s.name for s in resolved.entry.scopes] == ["oar_calendar.scope"]
    assert resolved.flow.client_id == "oar_calendar-cid"


@pytest.mark.asyncio()
async def test_connect_no_pin_filters_registrations_by_preferred_flow() -> None:
    """Only registrations offering the preferred flow count, so a device-flow
    app and an auth-code app for one slug are not ambiguous for either flow."""
    device = _gmail("oar_device")
    auth_code = _gmail("oar_ac", flow_kind="authorization_code")
    svc = _service(registrations=[device, auth_code])

    resolved = await svc.resolve_connect_source(
        "googleapis-com", preferred_flow="authorization_code"
    )

    assert resolved.registration is not None
    assert resolved.registration.id == auth_code.id
    assert resolved.entry.vendor == "googleapis.com/oar_ac"
    assert resolved.flow.client_id == "oar_ac-cid"


@pytest.mark.asyncio()
async def test_connect_no_pin_with_several_matching_registrations_is_ambiguous() -> None:
    svc = _service(registrations=[_gmail("oar_gmail"), _gmail("oar_calendar")])

    with pytest.raises(AmbiguousVendorError) as excinfo:
        await svc.resolve_connect_source("googleapis-com")

    assert excinfo.value.registration_ids == ["oar_gmail", "oar_calendar"]


@pytest.mark.asyncio()
async def test_connect_no_pin_ignores_inactive_registrations() -> None:
    inactive = _gmail("oar_old")
    inactive.is_active = False
    active = _gmail("oar_new")
    svc = _service(registrations=[inactive, active])

    resolved = await svc.resolve_connect_source("googleapis-com")

    assert resolved.registration is not None
    assert resolved.registration.id == active.id


@pytest.mark.asyncio()
async def test_connect_unknown_vendor_raises() -> None:
    svc = _service()

    with pytest.raises(UnknownVendorError):
        await svc.resolve_connect_source("github")


@pytest.mark.asyncio()
async def test_connect_config_without_flow_and_no_registration_is_unsupported() -> None:
    svc = _service(config_entries={"github": _github_config_entry()})

    with pytest.raises(UnsupportedFlowError):
        await svc.resolve_connect_source("github", preferred_flow="authorization_code")


# ---------------------------------------------------------------------------
# resolve_session_source: later steps re-open the session's own source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_session_source_without_pin_is_config_even_when_registration_added() -> None:
    """A config session keeps its config source — a registration added since
    (even one for another flow) never supplies its scopes or drops the
    identity probe."""
    reg = _FakeRegistration(
        api_vendor="github",
        name="Late GitHub App",
        flow_kind="authorization_code",
        client_id="late-cid",
        authorization_code_details=_FakeDetails(default_scopes=["late"]),
    )
    svc = _service(config_entries={"github": _github_config_entry()}, registrations=[reg])

    source = await svc.resolve_session_source(
        "github", registration_id=None, flow_kind="device_authorization"
    )

    assert source.registration is None
    assert source.entry.identity_probe is not None
    assert [s.name for s in source.entry.scopes] == ["repo:read", "repo:write"]


@pytest.mark.asyncio()
async def test_session_source_without_pin_raises_when_config_removed() -> None:
    """No fall-through to a registration when the config entry disappears."""
    svc = _service(registrations=[_gmail("oar_gmail")])

    with pytest.raises(UnknownVendorError):
        await svc.resolve_session_source("googleapis-com", registration_id=None)


@pytest.mark.asyncio()
async def test_session_source_with_pin_returns_that_registration() -> None:
    gmail, calendar = _gmail("oar_gmail"), _gmail("oar_calendar")
    svc = _service(registrations=[gmail, calendar])

    source = await svc.resolve_session_source(
        "googleapis-com", registration_id="oar_calendar", flow_kind="device_authorization"
    )

    assert source.registration is not None
    assert source.registration.id == calendar.id
    assert source.flow.client_id == "oar_calendar-cid"


@pytest.mark.asyncio()
async def test_session_source_with_inactive_pin_raises() -> None:
    reg = _gmail("oar_gmail")
    reg.is_active = False
    svc = _service(registrations=[reg])

    with pytest.raises(InvalidOAuthAppRegistrationError):
        await svc.resolve_session_source("googleapis-com", registration_id="oar_gmail")


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
# resolve_by_pin / list_all: VendorAuthConfig shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_resolve_by_pin_returns_config_when_no_db_row() -> None:
    """The config entry surfaces as ``VendorAuthConfig`` unchanged."""
    svc = _service(config_entries={"github": _github_config_entry()})
    entry = await svc.resolve_by_pin("github")
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
async def test_resolve_by_pin_raises_unknown_vendor_when_missing() -> None:
    svc = _service()
    with pytest.raises(UnknownVendorError):
        await svc.resolve_by_pin("github")


@pytest.mark.asyncio()
async def test_pinned_registration_projects_without_config_merge() -> None:
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
    entry = await svc.resolve_by_pin("github", registration_id=github_db.id)
    # DB registration is standalone; nothing bleeds in from the config.
    assert entry.display_name == "MyOrg GitHub"
    assert entry.flows[0].client_id == "db-cid"
    assert entry.vendor == "github.com/api.github.com"
    # No config merge → no identity probe, no config scope catalog.
    assert entry.identity_probe is None
    assert [s.name for s in entry.scopes] == ["repo"]


@pytest.mark.asyncio()
async def test_db_only_vendor_projects_with_catalog_api_id() -> None:
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
    entry = await svc.resolve_by_pin("notion")
    assert entry.display_name == "Notion"
    # ``vendor`` on the projected config is the admin-picked catalog API id.
    # This is the value stamped onto ``credential.catalog_api_id`` at connect
    # time, so the operations preview resolves against a real registered API.
    assert entry.vendor == "notion.com/api.notion.com"
    assert entry.flows[0].client_id == "notion-cid"
    # Admin registrations never carry an identity probe — that's a
    # platform-config concern. Credentials land with connected_as=None.
    assert entry.identity_probe is None


# ---------------------------------------------------------------------------
# Config-source projection edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_config_projection_carries_default_scopes_when_marked() -> None:
    """VendorEntry.default_scopes for config = scopes flagged ``default=True``."""
    svc = _service(config_entries={"github": _github_config_entry()})
    [entry] = await svc.list_entries()
    assert entry.default_scopes == ["repo:read"]


@pytest.mark.asyncio()
async def test_config_projection_omits_scopes_when_none_default() -> None:
    """Slack config carries no scopes at all → default_scopes is None."""
    svc = _service(config_entries={"slack": _slack_config_entry()})
    [entry] = await svc.list_entries()
    assert entry.default_scopes is None


@pytest.mark.asyncio()
async def test_projected_entry_is_a_pydantic_model() -> None:
    """The unified view is a Pydantic model so callers get schema validation."""
    svc = _service(config_entries={"github": _github_config_entry()})
    [entry] = await svc.list_entries()
    assert isinstance(entry, VendorEntry)


# ---------------------------------------------------------------------------
# resolve_by_pin: the single seam every vendor read routes through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_resolve_by_pin_returns_pinned_registration_over_preferred() -> None:
    """Two Gmail registrations with different default_scopes — pinning the
    non-preferred one returns *its* scopes, not the preferred-active one's.

    The scope-per-pin invariant is flow-kind-agnostic; device flow is used
    for brevity.
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

    # Pinning sandbox returns sandbox's scopes + client_id, not prod's.
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
    the other registration's scope (readonly) is rejected as unknown."""
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

    source = await svc.resolve_session_source("googleapis-com", registration_id="oar_sandbox")
    assert svc.validate_scopes(source.entry, ["mail.readonly"]) == ["mail.readonly"]
    # And the sandbox-native scope validates cleanly under the same pin.
    assert svc.validate_scopes(source.entry, ["mail.send"]) == []


# ---------------------------------------------------------------------------
# Client secret is never decrypted on vendor reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_vendor_reads_never_decrypt_registration_secret() -> None:
    """Listing, scope checks and flow resolution must not touch the secret.

    The ciphertext is garbage and ``ctx.encryption`` is not configured, so
    any decrypt on these read paths would raise. The projected auth-code
    flow carries an empty placeholder; the connect path decrypts lazily at
    token exchange.
    """
    reg = _FakeRegistration(
        api_vendor="googleapis-com",
        id="oar_ac",
        name="MyOrg Gmail",
        flow_kind="authorization_code",
        client_id="ac-cid",
        authorization_code_details=_FakeDetails(
            default_scopes=["gmail.readonly"],
            encrypted_client_secret="not-a-real-ciphertext",  # pragma: allowlist secret
        ),
    )
    svc = _service(registrations=[reg])

    assert [e.display_name for e in await svc.list_all()] == ["Example (DB)"]
    resolved = await svc.resolve_connect_source("googleapis-com")
    session = await svc.resolve_session_source("googleapis-com", registration_id="oar_ac")
    assert svc.validate_scopes(session.entry, ["gmail.readonly"]) == []
    assert [s.name for s in svc.merge_scopes(session.entry, None)] == ["gmail.readonly"]

    for f in (resolved.flow, session.flow):
        assert isinstance(f, VendorAuthorizationCodeFlowConfig)
        assert f.client_secret.get_secret_value() == ""
