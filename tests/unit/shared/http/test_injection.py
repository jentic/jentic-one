"""Unit tests for ``apply_injection``, the outbound-auth step the sync router
and the async worker share (#1957).

Header creds overlay the caller's headers in any letter case, query creds join
the URL query, and cookie creds are **appended** to upstream-learned cookies
whose actor, origin/domain, path, Secure, and expiry scope match. Unknown
cookies fail closed, and all retained cookies share exactly one ``Cookie``
header.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from urllib.parse import parse_qsl, urlparse

import pytest

import jentic_one.shared.http.injection as injection_mod
from jentic_one.shared.http.injection import (
    apply_injection,
    clear_upstream_cookie_scopes,
    record_upstream_set_cookie,
)
from jentic_one.shared.jobs.protocols import InjectedAuth

_URL = "https://api.x.com/v1"


@pytest.fixture(autouse=True)
def _isolate_cookie_registry() -> None:
    clear_upstream_cookie_scopes()


def _auth(
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    server_variables: dict[str, str] | None = None,
) -> InjectedAuth:
    return InjectedAuth(
        headers=headers or {},
        query_params=query or {},
        cookies=cookies or {},
        server_variables=server_variables,
    )


def test_header_creds_overlay_the_caller_headers_in_any_case() -> None:
    url, headers = apply_injection(
        {"accept": "application/json", "x-api-key": "caller"},
        _URL,
        _auth(headers={"X-Api-Key": "k"}),
        inbound_cookie=None,
    )
    assert url == _URL
    assert headers == {"accept": "application/json", "X-Api-Key": "k"}


def test_query_creds_join_the_url_query() -> None:
    url, headers = apply_injection(
        {}, f"{_URL}?page=2", _auth(query={"api_key": "k"}), inbound_cookie=None
    )
    assert url == f"{_URL}?page=2&api_key=k"
    assert headers == {}


def test_query_creds_start_a_query_when_there_is_none() -> None:
    url, _ = apply_injection({}, _URL, _auth(query={"api_key": "k"}), inbound_cookie=None)
    assert url == f"{_URL}?api_key=k"


def test_cookie_creds_append_to_a_learned_inbound_cookie() -> None:
    record_upstream_set_cookie(_URL, "forwarded=1; Path=/")
    _, headers = apply_injection(
        {}, _URL, _auth(cookies={"session": "s"}), inbound_cookie="forwarded=1"
    )
    assert headers == {"Cookie": "forwarded=1; session=s"}


def test_cookie_creds_alone_when_there_is_no_inbound_cookie() -> None:
    _, headers = apply_injection({}, _URL, _auth(cookies={"session": "s"}), inbound_cookie=None)
    assert headers == {"Cookie": "session=s"}


def test_unknown_inbound_cookie_is_dropped_without_origin_evidence() -> None:
    clear_upstream_cookie_scopes()
    _, headers = apply_injection({}, _URL, _auth(), inbound_cookie="sid=1")
    assert headers == {}


def test_cookie_already_in_headers_is_replaced_never_sent_twice() -> None:
    _, headers = apply_injection(
        {"cookie": "sid=1", "accept": "*/*"},
        _URL,
        _auth(cookies={"session": "s"}),
        inbound_cookie="sid=1",
    )
    assert headers == {"accept": "*/*", "Cookie": "session=s"}


def test_no_cookie_at_all_sends_no_cookie_header() -> None:
    _, headers = apply_injection({"accept": "*/*"}, _URL, _auth(), inbound_cookie="  ")
    assert headers == {"accept": "*/*"}


def test_server_variables_then_defaults_fill_the_url_template() -> None:
    url, _ = apply_injection(
        {},
        "https://{region}.api.x.com/{version}",
        _auth(server_variables={"region": "eu"}),
        inbound_cookie=None,
        server_variable_defaults={"region": "us", "version": "v2"},
    )
    assert url == "https://eu.api.x.com/v2"


def test_inputs_are_not_mutated() -> None:
    inbound = {"cookie": "sid=1"}
    auth = _auth(headers={"X-Api-Key": "k"}, cookies={"session": "s"})
    apply_injection(inbound, _URL, auth, inbound_cookie="sid=1")
    assert inbound == {"cookie": "sid=1"}
    assert auth.headers == {"X-Api-Key": "k"}


def test_duplicate_caller_query_params_are_removed_before_injection() -> None:
    url, _ = apply_injection(
        {},
        f"{_URL}?api_key=caller1&page=2&%61pi_key=caller2&api%5Fkey=caller3",
        _auth(query={"api_key": "broker"}),  # pragma: allowlist secret
        inbound_cookie=None,
    )
    assert url == f"{_URL}?page=2&api_key=broker"
    # A first-value-wins upstream parser sees only the injected credential.
    parsed_pairs = parse_qsl(urlparse(url).query, keep_blank_values=True)
    first_wins: dict[str, str] = {}
    for k, v in parsed_pairs:
        first_wins.setdefault(k, v)
    assert first_wins["api_key"] == "broker"  # pragma: allowlist secret
    assert [v for k, v in parsed_pairs if k == "api_key"] == ["broker"]


def test_duplicate_caller_cookies_are_removed_before_injection() -> None:
    _, headers = apply_injection(
        {},
        _URL,
        _auth(cookies={"session": "broker"}),
        inbound_cookie="session=caller1; theme=dark; %73ession=caller2; session=caller3",
    )
    assert headers == {"Cookie": "session=broker"}


def test_duplicate_caller_cookies_in_headers_mapping_are_removed() -> None:
    _, headers = apply_injection(
        {"Cookie": "session=caller; sid=keep"},
        _URL,
        _auth(cookies={"session": "broker"}),
        inbound_cookie=None,
    )
    assert headers == {"Cookie": "session=broker"}


def test_upstream_set_cookie_is_isolated_to_issuing_origin_and_path() -> None:
    clear_upstream_cookie_scopes()
    try:
        record_upstream_set_cookie(
            "https://a.example.com/v1/login",
            "session=alice-secret; Path=/; HttpOnly",
        )
        record_upstream_set_cookie(
            "https://a.example.com/admin/login",
            "admin_token=top-secret; Path=/admin; Secure",
        )

        # Same upstream origin receives the root-scoped cookie.
        _, same_origin_headers = apply_injection(
            {},
            "https://a.example.com/v1/profile",
            _auth(),
            inbound_cookie="session=alice-secret; admin_token=top-secret",
        )
        assert same_origin_headers == {"Cookie": "session=alice-secret"}

        # Same upstream origin under /admin over https receives both cookies.
        _, admin_headers = apply_injection(
            {},
            "https://a.example.com/admin/users",
            _auth(),
            inbound_cookie="session=alice-secret; admin_token=top-secret",
        )
        assert admin_headers == {"Cookie": "session=alice-secret; admin_token=top-secret"}

        # Insecure http on same host does not receive Secure cookie.
        _, insecure_headers = apply_injection(
            {},
            "http://a.example.com:443/admin/users",
            _auth(),
            inbound_cookie="admin_token=top-secret",
        )
        assert "Cookie" not in insecure_headers

        # Unrelated upstream B receives none of upstream A's cookies.
        _, cross_origin_headers = apply_injection(
            {},
            "https://b.example.com/v1/items",
            _auth(),
            inbound_cookie="session=alice-secret; admin_token=top-secret",
        )
        assert "Cookie" not in cross_origin_headers
    finally:
        clear_upstream_cookie_scopes()


def test_upstream_cookie_default_path_uses_the_request_directory() -> None:
    record_upstream_set_cookie("https://a.example.com/admin/login", "sid=1; HttpOnly")
    _, allowed = apply_injection(
        {}, "https://a.example.com/admin/users", _auth(), inbound_cookie="sid=1"
    )
    _, denied = apply_injection(
        {}, "https://a.example.com/public/users", _auth(), inbound_cookie="sid=1"
    )
    assert allowed == {"Cookie": "sid=1"}
    assert denied == {}


def test_forgetting_cookie_scopes_fails_closed_instead_of_leaking_across_origins() -> None:
    record_upstream_set_cookie("https://a.example.com/login", "sid=1; Path=/")
    clear_upstream_cookie_scopes()
    _, headers = apply_injection(
        {}, "https://b.example.com/profile", _auth(), inbound_cookie="sid=1"
    )
    assert headers == {}


def test_cross_actor_isolation_blocks_cookie_replay_across_actors_and_credentials() -> None:
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/login",
        "sid=alice-session; Path=/",
        actor_id="actor-a",
        credential_id="cred-1",
    )

    # Actor A with the matching credential forwards the cookie.
    _, actor_a_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="sid=alice-session",
        actor_id="actor-a",
        credential_id="cred-1",
    )
    assert actor_a_headers == {"Cookie": "sid=alice-session"}

    # Actor B cannot replay Actor A's recorded cookie pair.
    _, actor_b_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="sid=alice-session",
        actor_id="actor-b",
        credential_id="cred-1",
    )
    assert actor_b_headers == {}

    # Different credential under Actor A is isolated as well.
    _, other_cred_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="sid=alice-session",
        actor_id="actor-a",
        credential_id="cred-2",
    )
    assert other_cred_headers == {}

    # Unscoped caller cannot replay Actor A's recorded cookie pair.
    _, unscoped_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="sid=alice-session",
    )
    assert unscoped_headers == {}


def test_noisy_neighbor_cookie_flood_does_not_evict_other_actor_cookies() -> None:
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/login",
        "sid=bob-session; Path=/",
        actor_id="actor-b",
    )

    # Actor A floods well past the 256 per-partition cap.
    for idx in range(320):
        record_upstream_set_cookie(
            "https://noisy.vendor.com/v1/items",
            f"flood_{idx}=val_{idx}; Path=/",
            actor_id="actor-a",
        )

    # Actor B's cookie survives untouched.
    _, bob_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/profile",
        _auth(),
        inbound_cookie="sid=bob-session",
        actor_id="actor-b",
    )
    assert bob_headers == {"Cookie": "sid=bob-session"}

    # Actor A's earliest cookies are evicted while its newest cookies remain bounded at 256.
    _, a_early = apply_injection(
        {},
        "https://noisy.vendor.com/v1/items",
        _auth(),
        inbound_cookie="flood_0=val_0",
        actor_id="actor-a",
    )
    _, a_recent = apply_injection(
        {},
        "https://noisy.vendor.com/v1/items",
        _auth(),
        inbound_cookie="flood_319=val_319",
        actor_id="actor-a",
    )
    assert a_early == {}
    assert a_recent == {"Cookie": "flood_319=val_319"}


def test_actor_partition_lru_bounds_active_partitions() -> None:
    for idx in range(260):
        record_upstream_set_cookie(
            "https://api.vendor.com/v1/login",
            f"sid=val_{idx}; Path=/",
            actor_id=f"actor-{idx}",
        )

    _, evicted = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="sid=val_0",
        actor_id="actor-0",
    )
    _, retained = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="sid=val_259",
        actor_id="actor-259",
    )
    assert evicted == {}
    assert retained == {"Cookie": "sid=val_259"}


@pytest.mark.parametrize(
    "deletion_header",
    [
        "sid=secret; Max-Age=0; Path=/",
        "sid=; Max-Age=0",
        "sid=deleted; Max-Age=-1; Path=/",
        "sid=; Max-Age=-3600",
        "sid=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/",
    ],
)
def test_max_age_zero_negative_and_past_expires_immediately_evict_tracked_cookies(
    deletion_header: str,
) -> None:
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/login",
        "sid=secret; Path=/",
        actor_id="actor-a",
    )
    record_upstream_set_cookie(
        "https://other.vendor.com/v1/login",
        "sid=secret; Path=/",
        actor_id="actor-a",
    )
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/login",
        "sid=secret; Path=/",
        actor_id="actor-b",
    )

    # Upstream logout / deletion evicts only the matching actor and host scope.
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/logout",
        deletion_header,
        actor_id="actor-a",
    )

    _, evicted_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/profile",
        _auth(),
        inbound_cookie="sid=secret",
        actor_id="actor-a",
    )
    assert evicted_headers == {}

    # Unrelated host for the same actor and same host for Actor B remain intact.
    _, other_host_headers = apply_injection(
        {},
        "https://other.vendor.com/v1/profile",
        _auth(),
        inbound_cookie="sid=secret",
        actor_id="actor-a",
    )
    _, actor_b_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/profile",
        _auth(),
        inbound_cookie="sid=secret",
        actor_id="actor-b",
    )
    assert other_host_headers == {"Cookie": "sid=secret"}
    assert actor_b_headers == {"Cookie": "sid=secret"}


def test_stored_expiry_from_max_age_and_future_expires_invalidates_cookie_once_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_now = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
    current_now = base_now
    monkeypatch.setattr(injection_mod, "_utc_now", lambda: current_now)

    future_http_date = format_datetime(base_now + timedelta(seconds=30), usegmt=True)
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/login",
        "max_age_sid=m1; Path=/; Max-Age=60",
        actor_id="actor-a",
    )
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/login",
        f"expires_sid=e1; Path=/; Expires={future_http_date}",
        actor_id="actor-a",
    )

    _, initial_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="max_age_sid=m1; expires_sid=e1",
        actor_id="actor-a",
    )
    assert initial_headers == {"Cookie": "max_age_sid=m1; expires_sid=e1"}

    # Advance 31 seconds: Expires cookie has elapsed, Max-Age=60 cookie is still live.
    current_now = base_now + timedelta(seconds=31)
    _, mid_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="max_age_sid=m1; expires_sid=e1",
        actor_id="actor-a",
    )
    assert mid_headers == {"Cookie": "max_age_sid=m1"}

    # Advance past 60 seconds: Max-Age cookie is now expired too.
    current_now = base_now + timedelta(seconds=61)
    _, final_headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="max_age_sid=m1; expires_sid=e1",
        actor_id="actor-a",
    )
    assert final_headers == {}


def test_max_age_takes_precedence_over_expires_per_rfc6265(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_now = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(injection_mod, "_utc_now", lambda: base_now)
    future_date = format_datetime(base_now + timedelta(hours=1), usegmt=True)

    record_upstream_set_cookie(
        "https://api.vendor.com/v1/login",
        "sid=1; Path=/",
    )
    # Max-Age=0 wins even when Expires is in the future.
    record_upstream_set_cookie(
        "https://api.vendor.com/v1/logout",
        f"sid=1; Path=/; Expires={future_date}; Max-Age=0",
    )
    _, headers = apply_injection(
        {},
        "https://api.vendor.com/v1/items",
        _auth(),
        inbound_cookie="sid=1",
    )
    assert headers == {}


def test_rfc6265_domain_attribute_forwards_across_matching_subdomains_and_enforces_secure() -> None:
    record_upstream_set_cookie(
        "https://auth.vendor.com/oauth/login",
        "sso=vendor-token; Domain=.Vendor.COM; Path=/; Secure",
        actor_id="actor-a",
    )

    # Forwards to sibling subdomain and apex domain over https.
    _, sibling_https = apply_injection(
        {},
        "https://api.vendor.com/v1/users",
        _auth(),
        inbound_cookie="sso=vendor-token",
        actor_id="actor-a",
    )
    _, apex_https = apply_injection(
        {},
        "https://vendor.com/v1/users",
        _auth(),
        inbound_cookie="sso=vendor-token",
        actor_id="actor-a",
    )
    assert sibling_https == {"Cookie": "sso=vendor-token"}
    assert apex_https == {"Cookie": "sso=vendor-token"}

    # Secure attribute blocks forwarding over plaintext http to any subdomain.
    _, sibling_http = apply_injection(
        {},
        "http://api.vendor.com/v1/users",
        _auth(),
        inbound_cookie="sso=vendor-token",
        actor_id="actor-a",
    )
    _, issuing_http = apply_injection(
        {},
        "http://auth.vendor.com/oauth/users",
        _auth(),
        inbound_cookie="sso=vendor-token",
        actor_id="actor-a",
    )
    assert sibling_http == {}
    assert issuing_http == {}

    # Lookalike suffix without dot boundary does not domain-match.
    _, lookalike = apply_injection(
        {},
        "https://evilvendor.com/v1/users",
        _auth(),
        inbound_cookie="sso=vendor-token",
        actor_id="actor-a",
    )
    assert lookalike == {}


@pytest.mark.parametrize(
    ("issuing_url", "bad_domain"),
    [
        ("https://auth.vendor.com/login", "com"),
        ("https://auth.vendor.com/login", ".com"),
        ("https://auth.vendor.com/login", "evil.com"),
        ("https://auth.vendor.com/login", "other.vendor.org"),
        ("https://auth.vendor.com/login", "sub.auth.vendor.com"),
        ("https://127.0.0.1/login", "127.0.0.1"),
        ("https://127.0.0.1/login", "0.0.1"),
    ],
)
def test_rfc6265_rejects_public_suffix_foreign_and_ip_domain_attributes(
    issuing_url: str,
    bad_domain: str,
) -> None:
    record_upstream_set_cookie(
        issuing_url,
        f"sid=bad; Domain={bad_domain}; Path=/",
    )

    for candidate_target in (
        "https://api.vendor.com/v1",
        "https://auth.vendor.com/v1",
        "https://evil.com/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.1/v1",
    ):
        _, headers = apply_injection(
            {},
            candidate_target,
            _auth(),
            inbound_cookie="sid=bad",
        )
        assert headers == {}


def test_absent_domain_attribute_remains_strictly_host_only() -> None:
    record_upstream_set_cookie(
        "https://auth.vendor.com/login",
        "sid=host-only; Path=/",
    )
    _, same_host = apply_injection(
        {},
        "https://auth.vendor.com/profile",
        _auth(),
        inbound_cookie="sid=host-only",
    )
    _, sibling_subdomain = apply_injection(
        {},
        "https://api.vendor.com/profile",
        _auth(),
        inbound_cookie="sid=host-only",
    )
    _, parent_domain = apply_injection(
        {},
        "https://vendor.com/profile",
        _auth(),
        inbound_cookie="sid=host-only",
    )
    assert same_host == {"Cookie": "sid=host-only"}
    assert sibling_subdomain == {}
    assert parent_domain == {}


def test_foreign_host_cannot_evict_vendor_domain_cookie_via_forged_domain_max_age() -> None:
    record_upstream_set_cookie(
        "https://auth.vendor.com/login",
        "sid=keep; Domain=vendor.com; Path=/",
    )
    record_upstream_set_cookie(
        "https://evil.com/logout",
        "sid=; Domain=vendor.com; Path=/; Max-Age=0",
    )
    _, headers = apply_injection(
        {},
        "https://api.vendor.com/items",
        _auth(),
        inbound_cookie="sid=keep",
    )
    assert headers == {"Cookie": "sid=keep"}
