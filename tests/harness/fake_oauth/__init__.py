"""Test-only fake OAuth 2.0 authorization server.

A DB-less stand-in for a vendor's OAuth server, used by the real-backend UI
e2e (``ui/e2e/docker/connect-*.spec.ts``) to drive connect sessions to
``connected`` without a real vendor. It implements just enough of RFC 6749
(authorization code, refresh) and RFC 8628 (device authorization) and serves
an OpenAPI document whose ``oauth2`` scheme points back at itself, so the
same process is both the API and its authorization server.

Like ``tests.harness.smoke_upstream`` it does not import ``jentic_one``.
"""
