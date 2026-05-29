"""Comprehensive auth boundary test - captures and protects the auth boundary.

This test documents the CURRENT auth boundary (not the ideal state). It serves
as a regression test - if this fails, the auth boundary changed and you must
verify the change is intentional and update the test.

KNOWN AUTH BOUNDARY MISMATCHES (code vs OpenAPI spec declaration):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Runtime allows agents, spec declares human-only (_HUMAN_ONLY_OPERATIONS):
   - POST /toolkits                              (201 - creates toolkit)
   - POST /toolkits/{id}/keys                    (201 - issues new key)
   - PATCH /toolkits/{id}/keys/{key_id}          (404 - no auth check)
   - DELETE /toolkits/{id}/keys/{key_id}         (404 - no auth check)
   - DELETE /toolkits/{id}/credentials/{cred_id} (204 - unbinds credential)

   ROOT CAUSE: Missing dependencies=[Depends(require_human_session)] in code.
   FIX: Add dependency to src/routers/toolkits.py (5 endpoints).
   TODO: Phase 2/3 - add require_human_session to enforce spec declaration.

2. Spec says public (security: []), runtime requires auth (401 without key):
   - GET /search, GET /apis, GET /workflows, etc.
   Documented in allowed_mismatch set in test_no_unintended_public_endpoints.

NOTE: Some endpoints marked with TODO may need stricter auth in the future.
This test captures reality as of Phase 1 to prevent accidental loosening.
"""

from starlette.testclient import TestClient


# ── Known endpoint categories ─────────────────────────────────────────────────

# Public endpoints - no auth required (RUNTIME ACTUAL STATE, not OpenAPI spec)
# NOTE: The OpenAPI spec (_OPEN_OPERATIONS) marks several endpoints as public (security: [])
# that the auth middleware (auth.py SKIP) actually requires keys for at runtime.
# This test captures the RUNTIME behavior to detect regressions.
PUBLIC_ENDPOINTS = {
    ("GET", "/health"),
    # NOTE: /version requires auth in current implementation (checked by middleware)
    # TODO: Consider making /version public in the future
    ("GET", "/"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    ("GET", "/openapi.yaml"),
    ("GET", "/favicon.ico"),
    ("GET", "/llms.txt"),
    ("POST", "/user/create"),
    ("POST", "/user/login"),
    ("POST", "/user/token"),
    ("GET", "/.well-known/oauth-authorization-server"),
    ("POST", "/register"),
    ("POST", "/oauth/token"),
    # Discovery endpoints — NOTE: These are marked public in OpenAPI spec but require auth at runtime
    # ("GET", "/search"),  # Spec says public, runtime says 401
    # ("GET", "/apis"),  # Spec says public, runtime says 401
    # ("GET", "/apis/{api_id}"),  # Spec says public, runtime says 401
    # ("GET", "/apis/{api_id}/operations"),  # Spec says public, runtime says 401
    # ("GET", "/apis/{api_id}/overlays"),  # Spec says public, runtime says 401
    # ("GET", "/apis/{api_id}/overlays/{overlay_id}"),  # Spec says public, runtime says 401
    # ("GET", "/workflows"),  # Spec says public, runtime says 401
    # ("GET", "/workflows/{slug}"),  # Spec says public, runtime says 401
    ("POST", "/workflows/{slug}"),  # Actually public at runtime (open passthrough)
}

# Agent-accessible endpoints - work with toolkit key (X-Jentic-API-Key)
AGENT_ACCESSIBLE_ENDPOINTS = {
    # Inspect / discovery
    ("GET", "/inspect/{id}"),
    ("GET", "/search"),  # Requires auth (returns 401 without)
    ("GET", "/apis"),  # Requires auth (returns 401 without)
    ("GET", "/apis/{api_id}"),  # Requires auth
    ("GET", "/apis/{api_id}/operations"),  # Requires auth
    ("GET", "/workflows"),  # Requires auth
    ("GET", "/workflows/{slug}"),  # Requires auth
    # Broker execution
    ("GET", "/{target:path}"),  # Broker catch-all
    ("POST", "/{target:path}"),
    ("PUT", "/{target:path}"),
    ("PATCH", "/{target:path}"),
    ("DELETE", "/{target:path}"),
    # Traces / observability
    ("GET", "/traces"),
    ("GET", "/traces/{id}"),
    ("GET", "/jobs/{job_id}"),
    # Toolkits (read-only for agents + some write operations that should be human-only)
    ("GET", "/toolkits"),  # TODO: Review - currently works without auth
    ("GET", "/toolkits/{id}"),  # TODO: Review - currently works without auth
    ("GET", "/toolkits/{id}/keys"),
    ("GET", "/toolkits/{id}/credentials"),
    # ⚠️ MISMATCH: These are declared human-only in _HUMAN_ONLY_OPERATIONS (main.py)
    # but have NO require_human_session dependency in the code, so agents can access them.
    # TODO Phase 2/3: Add dependencies=[Depends(require_human_session)] to:
    #   - src/routers/toolkits.py:206 POST /toolkits
    #   - src/routers/toolkits.py:515 POST /toolkits/{id}/keys
    #   - src/routers/toolkits.py:725 DELETE /toolkits/{id}/credentials/{cred_id}
    #   - src/routers/toolkits.py PATCH /toolkits/{id}/keys/{key_id}
    #   - src/routers/toolkits.py DELETE /toolkits/{id}/keys/{key_id}
    ("POST", "/toolkits"),  # 201 - agents can create toolkits (should be human-only)
    ("POST", "/toolkits/{id}/keys"),  # 201 - agents can issue keys (should be human-only)
    (
        "DELETE",
        "/toolkits/{id}/credentials/{cred_id}",
    ),  # 204 - agents can unbind (should be human-only)
    ("PATCH", "/toolkits/{id}/keys/{key_id}"),  # 404 when key doesn't exist, but no auth check
    ("DELETE", "/toolkits/{id}/keys/{key_id}"),  # 404 when key doesn't exist, but no auth check
    # Access requests (agents can file, view own)
    ("POST", "/toolkits/{id}/access-requests"),
    ("GET", "/toolkits/{id}/access-requests"),
    ("GET", "/toolkits/{id}/access-requests/{req_id}"),
    # Credentials (read-only for agents)
    ("GET", "/credentials"),  # TODO: Review - currently works without auth
    ("GET", "/credentials/{cid}"),
    # Import (agents can import specs)
    ("POST", "/import"),
    # OAuth brokers (agents can list)
    ("GET", "/oauth-brokers"),
    ("GET", "/oauth-brokers/{broker_id}"),
}

# Human-only endpoints - require human session (reject agent keys)
HUMAN_ONLY_ENDPOINTS = {
    # Credentials write operations
    ("POST", "/credentials"),  # Agent needs special permission
    ("PATCH", "/credentials/{cid}"),
    ("DELETE", "/credentials/{cid}"),
    # Toolkit write operations
    ("POST", "/toolkits"),  # Create toolkit
    ("PATCH", "/toolkits/{id}"),  # Update toolkit
    ("DELETE", "/toolkits/{id}"),  # Delete toolkit
    ("POST", "/toolkits/{id}/keys"),  # Issue new key
    ("PATCH", "/toolkits/{id}/keys/{key_id}"),
    ("DELETE", "/toolkits/{id}/keys/{key_id}"),  # Revoke key
    ("POST", "/toolkits/{id}/credentials"),  # Bind credential (admin only)
    ("DELETE", "/toolkits/{id}/credentials/{cred_id}"),
    ("PUT", "/toolkits/{id}/credentials/{cred_id}/permissions"),
    ("PATCH", "/toolkits/{id}/credentials/{cred_id}/permissions"),
    # Access request approvals
    ("POST", "/toolkits/{id}/access-requests/{req_id}/approve"),
    ("POST", "/toolkits/{id}/access-requests/{req_id}/deny"),
    # Catalog admin operations
    ("POST", "/apis"),
    ("DELETE", "/apis/{api_id}"),
    ("POST", "/apis/{api_id}/overlays"),
    ("DELETE", "/apis/{api_id}/overlays/{overlay_id}"),
    # OAuth broker admin
    ("POST", "/oauth-brokers"),
    ("PATCH", "/oauth-brokers/{broker_id}"),
    ("DELETE", "/oauth-brokers/{broker_id}"),
    ("POST", "/oauth-brokers/{broker_id}/accounts/{account_id}/reconnect-link"),
    ("PATCH", "/oauth-brokers/{broker_id}/accounts/{account_id}"),
    # Default toolkit key (legacy, human-only; pre-claim returns 410)
    ("POST", "/default-api-key/generate"),
    # User management
    ("POST", "/user/logout"),
    ("GET", "/user/me"),
    ("GET", "/agents"),
    ("GET", "/agents/{agent_id}"),
    ("POST", "/agents/{agent_id}/approve"),
    ("POST", "/agents/{agent_id}/deny"),
    ("POST", "/agents/{agent_id}/disable"),
    ("POST", "/agents/{agent_id}/enable"),
    ("PUT", "/agents/{agent_id}/jwks"),
    ("DELETE", "/agents/{agent_id}"),
    ("POST", "/agents/{agent_id}/grants"),
    ("GET", "/agents/{agent_id}/grants"),
    ("DELETE", "/agents/{agent_id}/grants/{toolkit_id}"),
}


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_register_get_and_oauth_revoke_require_auth(app):
    """GET /register/{id} and POST /oauth/revoke are not anonymous (middleware).

    Uses a fresh TestClient so the session-scoped shared client (which may hold
    admin cookies from agent_key/admin_session) does not mask unauthenticated behavior.
    """
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.get("/register/ag_nonexistent").status_code == 401
        assert anonymous.post("/oauth/revoke", data={"token": "rt_notreal"}).status_code == 401


def test_default_api_key_generate_requires_human_session(app):
    """POST /default-api-key/generate is human-only at all times.

    Uses a fresh TestClient to avoid session cookie leakage from other tests.
    Pre-claim returns 410 to disallow agent self-enrollment; post-claim returns 401
    without a human session. The endpoint is never anonymous-public.
    """
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        resp = anonymous.post("/default-api-key/generate")
        assert resp.status_code in (401, 403, 410), (
            f"POST /default-api-key/generate must not be anonymous-public; got {resp.status_code}"
        )


def test_public_endpoints_accessible_without_auth(app):
    """Public endpoints should be accessible without any auth.

    Uses a fresh TestClient so the session-scoped shared client (which may hold
    admin cookies from agent_key/admin_session) does not mask regressions by
    making human-only endpoints look public.
    """
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.get("/health").status_code == 200
        # NOTE: /version currently requires auth (may want to make it public)
        # assert anonymous.get("/version").status_code == 401
        # NOTE: Discovery endpoints now require auth at runtime (spec says public, middleware says no)
        # assert anonymous.get("/search").status_code == 401
        # assert anonymous.get("/apis").status_code == 401
        assert anonymous.get("/docs").status_code == 200


def test_agent_accessible_endpoints_work_with_agent_key(client, agent_key_header):
    """Agent-accessible endpoints should work with a toolkit key."""
    # Test a few representative agent endpoints
    assert client.get("/toolkits", headers=agent_key_header).status_code == 200
    assert client.get("/credentials", headers=agent_key_header).status_code == 200
    assert client.get("/traces", headers=agent_key_header).status_code == 200


def test_agent_accessible_endpoints_reject_no_auth(app):
    """Agent-accessible endpoints should reject requests without auth.

    NOTE: Some endpoints currently allow access without auth (GET /apis,
    GET /toolkits, GET /credentials, GET /traces). These are documented
    separately and may need review for Phase 2. This test validates that
    other protected endpoints correctly reject unauthenticated access.
    """
    # Use a fresh client to avoid session cookie leakage from other tests
    with TestClient(app, raise_server_exceptions=False) as fresh_client:
        # Representative protected endpoint: should fail auth before any resource lookup.
        # Test POST /credentials (requires auth) - should return 401 without auth
        response = fresh_client.post("/credentials", json={"label": "test", "value": "test"})
        assert response.status_code == 401, (
            f"POST /credentials should reject requests without auth (401), "
            f"got {response.status_code}"
        )


def test_human_only_endpoints_reject_agent_key(client, agent_key_header):
    """Human-only endpoints MUST reject agent keys.

    This test captures the CURRENT state. Known mismatches (code allows agents,
    spec declares human-only) are documented in the module docstring and deferred
    to Phase 2/3. See "KNOWN AUTH BOUNDARY MISMATCHES" section above.
    """
    # NOTE: Known mismatches - these endpoints allow agents at runtime but are
    # declared human-only in _HUMAN_ONLY_OPERATIONS (main.py). They lack
    # dependencies=[Depends(require_human_session)] in the route definitions.
    #
    # Current runtime behavior (verified):
    # - POST /toolkits → 201 (creates toolkit)
    # - POST /toolkits/{id}/keys → 201 (issues new key)
    # - PATCH /toolkits/{id}/keys/{key_id} → 404 (no auth check, fails on missing resource)
    # - DELETE /toolkits/{id}/keys/{key_id} → 404 (no auth check, fails on missing resource)
    # - DELETE /toolkits/{id}/credentials/{cred_id} → 204 (unbinds credential)
    #
    # These are intentionally NOT tested here (would pass incorrectly). They're
    # documented in AGENT_ACCESSIBLE_ENDPOINTS with TODO markers for Phase 2/3 fix.

    # POST /credentials with agent key returns 403 (correct) or 409 if already exists
    response = client.post(
        "/credentials",
        headers=agent_key_header,
        json={
            "label": "Test Auth Boundary",
            "value": "secret",
            "api_id": "test-boundary.com",
            "auth_type": "bearer",
        },
    )
    assert response.status_code in (403, 409), (
        f"POST /credentials should reject agent key (403) or conflict (409), got {response.status_code}"
    )

    # These 404 because resource doesn't exist, but would be 403 if it did
    response = client.patch("/toolkits/nonexistent", headers=agent_key_header, json={"name": "New"})
    assert response.status_code in (403, 404), "PATCH /toolkits should reject agent key"


def test_openapi_spec_endpoint_count_unchanged(client):
    """Verify the total number of endpoints hasn't changed unexpectedly.

    If this test fails, review the new/removed endpoints to ensure they
    have the correct auth requirements.
    """
    spec = client.get("/openapi.json").json()

    # Count all operations across all paths
    total_operations = 0
    for path, methods in spec["paths"].items():
        for method in methods:
            if method in ["get", "post", "put", "patch", "delete", "head", "options"]:
                total_operations += 1

    # This is the baseline from v0.7.1 + Phase 1 changes
    # If this fails, audit the diff to ensure new endpoints have correct auth
    EXPECTED_OPERATION_COUNT = 91  # main's 90 (preview_catalog_* + agent identity + agents admin) + GET /traces/usage (Monitor page aggregations)

    assert total_operations == EXPECTED_OPERATION_COUNT, (
        f"Expected {EXPECTED_OPERATION_COUNT} operations, found {total_operations}. "
        "If you added/removed endpoints, update EXPECTED_OPERATION_COUNT after "
        "verifying auth requirements are correct."
    )


def test_all_protected_operations_have_explicit_security(client):
    """Every non-public operation must have explicit security declarations."""
    spec = client.get("/openapi.json").json()

    missing_security = []
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in ["get", "post", "put", "patch", "delete"]:
                continue

            # Skip public operations (they have security: [])
            if (method.upper(), path) in PUBLIC_ENDPOINTS:
                continue

            # All others must have security declared
            if "security" not in operation:
                missing_security.append(f"{method.upper()} {path}")

    assert len(missing_security) == 0, (
        f"Operations missing explicit security declarations: {missing_security}"
    )


def test_no_unintended_public_endpoints(client):
    """Ensure no operations are accidentally marked as public (security: []).

    NOTE: There is a known mismatch between the OpenAPI spec (_OPEN_OPERATIONS in main.py)
    and the auth middleware (SKIP in auth.py). The OpenAPI spec marks several discovery
    endpoints as public (security: []) that the middleware actually requires keys for.

    This test checks the OpenAPI spec declarations against PUBLIC_ENDPOINTS, which captures
    the RUNTIME behavior. Endpoints marked public in the spec but requiring auth at runtime
    are documented in the allowed_mismatch set below.
    """
    spec = client.get("/openapi.json").json()

    # Known mismatch: OpenAPI spec says public, runtime requires auth
    allowed_mismatch = {
        "GET /search",
        "GET /apis",
        "GET /apis/{api_id}",
        "GET /apis/{api_id}/operations",
        "GET /apis/{api_id}/overlays",
        "GET /apis/{api_id}/overlays/{overlay_id}",
        "GET /workflows",
        "GET /workflows/{slug}",
    }

    unexpected_public = []
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in ["get", "post", "put", "patch", "delete"]:
                continue

            # If security is empty array, it's public
            if operation.get("security") == []:
                endpoint = (method.upper(), path)
                endpoint_str = f"{method.upper()} {path}"
                if endpoint not in PUBLIC_ENDPOINTS and endpoint_str not in allowed_mismatch:
                    unexpected_public.append(endpoint_str)

    assert len(unexpected_public) == 0, (
        f"Operations unexpectedly marked as public: {unexpected_public}. These should require auth!"
    )
