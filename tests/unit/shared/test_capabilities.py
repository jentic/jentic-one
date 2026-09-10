"""Unit tests for the public capability document (``GET /capabilities``, #1279).

One consolidated, unauthenticated deployment self-description (auth methods,
broker URL, surface composition, feature flags) so a client can onboard from a
single URL instead of probe-and-guess across ``/instance``, ``/auth/idp``, and
the RFC 8414 document. Config-only (no DB), so these run as fast units.
"""

from __future__ import annotations

import typing
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

import jentic_one.shared.web.capabilities as capabilities_module
from jentic_one import __version__
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.capabilities import (
    CAPABILITIES_VERSION,
    CapabilitiesResponse,
    CapabilityView,
    register_capability_contributor,
    resolve_capabilities,
)

_ALL_APPS = ["admin", "auth", "control", "registry"]


def _ctx(sample_config_dict: dict[str, Any], **overrides: Any) -> Context:
    """Context with ``auth``/``server`` sections deep-merged over the sample config."""
    cfg = dict(sample_config_dict)
    cfg["auth"] = {
        **cfg.get("auth", {}),
        "canonical_base_url": "http://127.0.0.1:8000",
        **overrides.get("auth", {}),
    }
    if "server" in overrides:
        cfg["server"] = {**cfg.get("server", {}), **overrides["server"]}
    return Context(AppConfig.model_validate(cfg))


@pytest.fixture(autouse=True)
def _isolated_contributor_registry() -> Iterator[None]:
    """Snapshot/restore the process-global contributor registry around each test."""
    saved = list(capabilities_module._capability_contributors)
    yield
    capabilities_module._capability_contributors[:] = saved


def test_capabilities_default_body_is_golden(sample_config_dict: dict[str, Any]) -> None:
    """Byte-shape golden of the default document (regression pin).

    The document is a client contract (login pickers and onboarding flows parse
    it), so any change to the default body must be deliberate — update this pin
    and bump ``capabilities_version`` if the shape changed incompatibly.
    """
    ctx = _ctx(sample_config_dict)
    client = TestClient(create_combined_app(ctx, _ALL_APPS), raise_server_exceptions=False)

    resp = client.get("/capabilities")

    assert resp.status_code == 200
    base = "http://127.0.0.1:8000"
    assert resp.json() == {
        "instance": {
            "backend": "local",
            "canonical_base_url": base,
        },
        "surfaces": ["admin", "auth", "control", "registry"],
        "urls": {
            "broker": None,
            "authorization_server_metadata": f"{base}/.well-known/oauth-authorization-server",
            "authorization_server_metadata_mcp": None,
            "protected_resource_metadata": None,
            "authorize": f"{base}/authorize",
            "token": f"{base}/oauth/token",
            "agent_registration": f"{base}/register",
            "oauth_client_registration": f"{base}/oauth-clients",
        },
        "auth": {
            "methods": {
                "idp": {"enabled": False, "provider": None},
                "local_login": {"enabled": False},
                "oauth_client_dcr": {"enabled": False, "approval": "manual"},
                "agent_dcr": {"enabled": True},
                "service_accounts": {"enabled": True},
            }
        },
        "features": {"mcp": False},
        "capabilities_version": 1,
    }


def test_capabilities_is_unauthenticated(sample_config_dict: dict[str, Any]) -> None:
    """No Authorization header required — it is the public onboarding document."""
    client = TestClient(
        create_combined_app(_ctx(sample_config_dict), _ALL_APPS),
        raise_server_exceptions=False,
    )
    assert client.get("/capabilities").status_code == 200


def test_capabilities_reflects_idp_enabled(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict, auth={"idp": {"enabled": True, "provider": "google"}})
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.auth.methods.idp.enabled is True
    assert doc.auth.methods.idp.provider == "google"


def test_capabilities_hides_provider_when_idp_disabled(
    sample_config_dict: dict[str, Any],
) -> None:
    """Mirrors ``GET /auth/idp``: a disabled IdP never names its provider."""
    ctx = _ctx(sample_config_dict, auth={"idp": {"enabled": False, "provider": "google"}})
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.auth.methods.idp.enabled is False
    assert doc.auth.methods.idp.provider is None


def test_capabilities_reflects_local_login_enabled(sample_config_dict: dict[str, Any]) -> None:
    """``auth.local_login.enabled`` with no IdP → the form is offered (#1276)."""
    ctx = _ctx(sample_config_dict, auth={"local_login": {"enabled": True}})
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.auth.methods.local_login.enabled is True


def test_capabilities_local_login_yields_to_idp(sample_config_dict: dict[str, Any]) -> None:
    """IdP always wins (no mixed mode): with an IdP enabled the login form is
    never reachable on ``/authorize``, so the document must not advertise it —
    this is the *effective* offer, not a raw config echo."""
    ctx = _ctx(
        sample_config_dict,
        auth={
            "local_login": {"enabled": True},
            "idp": {"enabled": True, "provider": "google"},
        },
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.auth.methods.idp.enabled is True
    assert doc.auth.methods.local_login.enabled is False


def test_capabilities_reflects_mcp_flags(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(
        sample_config_dict,
        server={"mcp": {"enabled": True, "oauth": {"enabled": True, "auto_approve_clients": True}}},
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.features["mcp"] is True
    assert doc.auth.methods.oauth_client_dcr.enabled is True
    assert doc.auth.methods.oauth_client_dcr.approval == "auto"


def test_capabilities_reflects_surface_composition(sample_config_dict: dict[str, Any]) -> None:
    """Without the auth surface this process has no login door to advertise —
    scope is the answering process: a sibling tier of a split deployment may
    still serve these (the document never claims deployment-wide absence)."""
    ctx = _ctx(sample_config_dict)
    doc = resolve_capabilities(ctx, ["registry", "control"])
    assert doc.surfaces == ["control", "registry"]  # sorted
    assert doc.urls.authorization_server_metadata is None
    assert doc.urls.authorize is None
    assert doc.urls.token is None
    assert doc.urls.agent_registration is None
    assert doc.urls.oauth_client_registration is None
    assert doc.auth.methods.agent_dcr.enabled is False
    assert doc.auth.methods.service_accounts.enabled is False


def test_capabilities_urls_are_absolute_when_base_url_known(
    sample_config_dict: dict[str, Any],
) -> None:
    """A client that reached the box by IP must be able to construct absolute
    URLs from the document alone — relative paths would force it back to the
    probing this document exists to eliminate."""
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    base = "http://127.0.0.1:8000"
    assert doc.urls.authorization_server_metadata == (
        f"{base}/.well-known/oauth-authorization-server"
    )
    assert doc.urls.authorize == f"{base}/authorize"


def test_capabilities_mcp_metadata_urls_follow_the_oauth_gate(
    sample_config_dict: dict[str, Any],
) -> None:
    """The /mcp-scoped RFC 8414 + RFC 9728 documents are published iff
    server.mcp.oauth is enabled — oauth_client_dcr registers at /oauth-clients
    (the /mcp issuer's door), not the root document's /register, so a DCR
    client must be pointed at the right metadata."""
    off = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert off.urls.authorization_server_metadata_mcp is None
    assert off.urls.protected_resource_metadata is None

    ctx = _ctx(sample_config_dict, server={"mcp": {"oauth": {"enabled": True}}})
    on = resolve_capabilities(ctx, _ALL_APPS)
    base = "http://127.0.0.1:8000"
    assert on.urls.authorization_server_metadata_mcp == (
        f"{base}/.well-known/oauth-authorization-server/mcp"
    )
    assert on.urls.protected_resource_metadata == (f"{base}/.well-known/oauth-protected-resource")


def test_capabilities_broker_url_publishes_advertised_key(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(
        sample_config_dict,
        server={"advertised_broker_url": "https://broker.jentic.example"},
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.urls.broker == "https://broker.jentic.example"


def test_capabilities_broker_url_is_null_unless_advertised(
    sample_config_dict: dict[str, Any],
) -> None:
    """The internal control-plane→broker hop URL (server.mcp.broker_url) is
    topology-private and never published — on any backend. server.backend is a
    self-declared hint, so it can never gate what an unauthenticated endpoint
    discloses."""
    for backend in ("local", "remote"):
        ctx = _ctx(sample_config_dict, server={"backend": backend})
        assert ctx.config.server.mcp.broker_url  # the hop URL exists…
        doc = resolve_capabilities(ctx, _ALL_APPS)
        assert doc.urls.broker is None  # …but is not leaked


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "broker.internal:8100",  # schemeless
        "https://",  # no host
    ],
)
def test_capabilities_broker_url_rejects_non_http_urls(
    sample_config_dict: dict[str, Any], bad_url: str
) -> None:
    """Only an absolute http(s) URL is publishable; anything else is dropped
    (logged at boot), never published verbatim."""
    ctx = _ctx(sample_config_dict, server={"advertised_broker_url": bad_url})
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.urls.broker is None


def test_capabilities_broker_url_strips_query_and_fragment(
    sample_config_dict: dict[str, Any],
) -> None:
    """A credential smuggled in a query string must not survive publication."""
    ctx = _ctx(
        sample_config_dict,
        server={"advertised_broker_url": "https://pub.example.com/broker?token=s3cret#frag"},
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.urls.broker == "https://pub.example.com/broker"
    assert "s3cret" not in (doc.urls.broker or "")


def test_capabilities_broker_url_strips_userinfo(sample_config_dict: dict[str, Any]) -> None:
    """Credentials embedded in a configured broker URL are never published."""
    ctx = _ctx(
        sample_config_dict,
        # pragma: allowlist nextline secret
        server={"advertised_broker_url": "https://user:secret@broker.jentic.example:8443"},
    )
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.urls.broker == "https://broker.jentic.example:8443"
    assert "secret" not in (doc.urls.broker or "")


def test_capabilities_instance_slice_strips_userinfo(
    sample_config_dict: dict[str, Any],
) -> None:
    cfg = dict(sample_config_dict)
    cfg["auth"] = {
        **cfg.get("auth", {}),
        # pragma: allowlist nextline secret
        "canonical_base_url": "https://user:secret@jentic.acme.example",
    }
    ctx = Context(AppConfig.model_validate(cfg))
    doc = resolve_capabilities(ctx, _ALL_APPS)
    assert doc.instance.canonical_base_url == "https://jentic.acme.example"


def test_contributor_extends_features(sample_config_dict: dict[str, Any]) -> None:
    """A registered contributor's keys surface in ``features`` (the seam contract)."""

    def contribute(view: CapabilityView) -> Mapping[str, bool]:
        return {"acme_sso": True}

    register_capability_contributor(contribute)
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features == {"mcp": False, "acme_sso": True}


def test_contributor_receives_a_frozen_view_not_the_context(
    sample_config_dict: dict[str, Any],
) -> None:
    """The seam hands out a read-only projection: a contributor behind an
    unauthenticated route must not be able to reach (let alone mutate) live
    security config through its argument."""
    seen: list[object] = []

    def contribute(view: CapabilityView) -> Mapping[str, bool]:
        seen.append(view)
        return {}

    register_capability_contributor(contribute)
    resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    (view,) = seen
    assert isinstance(view, CapabilityView)
    assert not isinstance(view, Context)
    with pytest.raises(Exception):  # noqa: B017 - frozen: any mutation must raise
        view.backend = "remote"  # type: ignore[misc,unused-ignore]


def test_contributor_cannot_override_built_in_keys(sample_config_dict: dict[str, Any]) -> None:
    """First writer wins: a contribution never rewrites a built-in flag's meaning."""

    def contribute(view: CapabilityView) -> Mapping[str, bool]:
        return {"mcp": True, "acme_extra": True}

    register_capability_contributor(contribute)
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features["mcp"] is False  # built-in value preserved
    assert doc.features["acme_extra"] is True  # non-colliding key still lands


def test_contributor_cannot_case_shadow_built_in_keys(
    sample_config_dict: dict[str, Any],
) -> None:
    """Collisions are case-insensitive: 'MCP' must not shadow 'mcp' for a
    case-insensitive client."""
    register_capability_contributor(lambda view: {"MCP": True})
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features == {"mcp": False}


def test_contributors_merge_in_registration_order(sample_config_dict: dict[str, Any]) -> None:
    register_capability_contributor(lambda view: {"shared_key": True})
    register_capability_contributor(lambda view: {"shared_key": False})
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features["shared_key"] is True


def test_contributor_failures_never_break_the_document(
    sample_config_dict: dict[str, Any],
) -> None:
    """Each contributor is isolated: a raising or non-mapping-returning
    contribution is logged and skipped — one bad downstream package must not
    500 a public endpoint — and later contributors still run."""

    def raises(view: CapabilityView) -> Mapping[str, bool]:
        raise RuntimeError("boom")

    register_capability_contributor(raises)
    register_capability_contributor(lambda view: None)  # type: ignore[arg-type]
    register_capability_contributor(lambda view: {"acme_ok": True})
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features == {"mcp": False, "acme_ok": True}


def test_contributor_non_str_keys_and_non_bool_values_are_dropped(
    sample_config_dict: dict[str, Any],
) -> None:
    """Content policy for the public document: feature flags are str→bool.
    Anything else (objects, ints, non-str keys) is dropped, never published."""
    register_capability_contributor(
        lambda view: {42: True, "acme_level": 3, "acme_obj": object(), "acme_ok": True}  # type: ignore[arg-type]
    )
    doc = resolve_capabilities(_ctx(sample_config_dict), _ALL_APPS)
    assert doc.features == {"mcp": False, "acme_ok": True}


def test_register_rejects_non_callables_and_wrong_arity() -> None:
    """Bad registrations fail loudly at registration time (matching the config
    extension and telemetry-event registries), not at request time."""
    with pytest.raises(TypeError):
        register_capability_contributor(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        register_capability_contributor(lambda: {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        register_capability_contributor(lambda a, b: {})  # type: ignore[arg-type]


def test_register_rejects_duplicate_contributor() -> None:
    def contribute(view: CapabilityView) -> Mapping[str, bool]:
        return {}

    register_capability_contributor(contribute)
    with pytest.raises(ValueError):
        register_capability_contributor(contribute)


def test_capabilities_present_on_standalone_surface(sample_config_dict: dict[str, Any]) -> None:
    """Mounted in standalone surface apps too — with that surface's composition."""
    client = TestClient(create_control_app(_ctx(sample_config_dict)), raise_server_exceptions=False)
    resp = client.get("/capabilities")
    assert resp.status_code == 200
    assert resp.json()["surfaces"] == ["control"]


def test_capabilities_not_mounted_on_broker(sample_config_dict: dict[str, Any]) -> None:
    """The broker data plane opts out exactly as it does for ``/instance``."""
    client = TestClient(create_broker_app(_ctx(sample_config_dict)), raise_server_exceptions=False)
    resp = client.get("/capabilities")
    assert resp.status_code != 200
    assert "capabilities_version" not in resp.text


def test_capabilities_never_publishes_a_version_string(
    sample_config_dict: dict[str, Any],
) -> None:
    """ASVS fingerprinting posture: no exact version (that stays behind auth)."""
    client = TestClient(
        create_combined_app(_ctx(sample_config_dict), _ALL_APPS),
        raise_server_exceptions=False,
    )
    assert __version__ not in client.get("/capabilities").text


def test_capabilities_schema_visible_and_public(sample_config_dict: dict[str, Any]) -> None:
    """It is a real (schema-visible) route stamped public (no BearerAuth)."""
    app = create_combined_app(_ctx(sample_config_dict), _ALL_APPS)
    op = app.openapi()["paths"]["/capabilities"]["get"]
    assert op["security"] == []
    assert op["tags"] == ["Discovery"]


def test_contributors_run_at_app_build_not_per_request(
    sample_config_dict: dict[str, Any],
) -> None:
    """The features map is resolved once when the router is built: contributors
    never run on the unauthenticated request path (no per-request cost, log
    volume, or event-loop exposure), and a registration arriving after the app
    exists contributes nothing to it."""
    calls: list[int] = []

    def counting(view: CapabilityView) -> Mapping[str, bool]:
        calls.append(1)
        return {"acme_early": True}

    register_capability_contributor(counting)
    client = TestClient(
        create_combined_app(_ctx(sample_config_dict), _ALL_APPS),
        raise_server_exceptions=False,
    )
    build_time_calls = len(calls)
    assert build_time_calls >= 1

    register_capability_contributor(lambda view: {"acme_late": True})
    first = client.get("/capabilities").json()["features"]
    second = client.get("/capabilities").json()["features"]
    assert first == second == {"mcp": False, "acme_early": True}
    assert "acme_late" not in first
    assert len(calls) == build_time_calls  # request path never re-runs contributors


def test_capabilities_sends_cache_headers_and_honours_if_none_match(
    sample_config_dict: dict[str, Any],
) -> None:
    """Every client fetches this before sign-in, so a fleet restart must be able
    to revalidate an unchanged body (304) instead of re-downloading it."""
    client = TestClient(
        create_combined_app(_ctx(sample_config_dict), _ALL_APPS),
        raise_server_exceptions=False,
    )
    resp = client.get("/capabilities")
    assert resp.headers["Cache-Control"] == "public, max-age=60"
    etag = resp.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')

    revalidation = client.get("/capabilities", headers={"If-None-Match": etag})
    assert revalidation.status_code == 304
    assert revalidation.content == b""

    miss = client.get("/capabilities", headers={"If-None-Match": '"stale"'})
    assert miss.status_code == 200
    assert miss.headers["ETag"] == etag


def _field_paths(model: type[BaseModel], prefix: str = "") -> Iterator[str]:
    """Every field pointer of a (nested) response model, e.g. 'urls.broker'."""
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        yield path
        for candidate in (field.annotation, *typing.get_args(field.annotation)):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                yield from _field_paths(candidate, f"{path}.")


def test_capabilities_version_is_tied_to_the_document_shape() -> None:
    """Pins (CAPABILITIES_VERSION, field-pointer set) so the integer cannot
    silently drift from the shape it versions: removing/renaming/retyping a
    field fails here, forcing a deliberate version bump (or pin update for an
    additive change)."""
    assert CAPABILITIES_VERSION == 1
    assert sorted(_field_paths(CapabilitiesResponse)) == [
        "auth",
        "auth.methods",
        "auth.methods.agent_dcr",
        "auth.methods.agent_dcr.enabled",
        "auth.methods.idp",
        "auth.methods.idp.enabled",
        "auth.methods.idp.provider",
        "auth.methods.local_login",
        "auth.methods.local_login.enabled",
        "auth.methods.oauth_client_dcr",
        "auth.methods.oauth_client_dcr.approval",
        "auth.methods.oauth_client_dcr.enabled",
        "auth.methods.service_accounts",
        "auth.methods.service_accounts.enabled",
        "capabilities_version",
        "features",
        "instance",
        "instance.backend",
        "instance.canonical_base_url",
        "surfaces",
        "urls",
        "urls.agent_registration",
        "urls.authorization_server_metadata",
        "urls.authorization_server_metadata_mcp",
        "urls.authorize",
        "urls.broker",
        "urls.oauth_client_registration",
        "urls.protected_resource_metadata",
        "urls.token",
    ], (
        "the capability document's shape changed: if a field was removed, renamed, "
        "or retyped, bump CAPABILITIES_VERSION; then update this pin"
    )
