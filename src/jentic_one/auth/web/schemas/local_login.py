"""Web schemas for the platform-session continuation exchange (#1299)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from jentic_one.shared.web import openapi_examples as ex


class OAuthSessionContinueRequest(BaseModel):
    """Front-channel session-continue exchange posted from the login page.

    ``state`` is the signed ``login``-purpose carry-through token (``ls``)
    minted by rung 3 of ``flow.resolve_identity_gate`` for this exact
    authorize request — the exchange never accepts bare flow parameters, so a
    caller cannot probe arbitrary client ids through it.

    ``state`` is deliberately NOT marked x-sensitive, for the same reason as
    ``OAuthApprovalDecisionRequest.state``: the CLI's redaction backstop
    unions bare field names globally, and the blob is not a lasting bearer
    credential (HMAC-signed, purpose-discriminated, TTL'd; the endpoint
    additionally requires an authenticated platform user).
    """

    state: str = Field(
        description="Signed login carry-through token (`ls`) issued for this authorize request.",
        examples=[ex.SIGNED_FLOW_STATE],
    )


class OAuthSessionContinueResponse(BaseModel):
    """The resume leg for a successful session-continue exchange.

    ``redirect_url`` is a same-origin, relative ``/authorize`` URL re-running
    the ORIGINAL authorize request plus the short-TTL ``session``-purpose
    continuation blob (``sc``) pinning the platform user at exchange time.
    Relative by design (mirrors the approval-pending page's resume URL): the
    page script navigates within its own origin, never to a caller-influenced
    absolute URL.
    """

    redirect_url: str = Field(
        description="Relative `/authorize` URL to navigate to in order to resume the flow.",
        examples=[
            f"/authorize?response_type=code&client_id={ex.OAUTH_PUBLIC_CLIENT_ID}"
            f"&state={ex.OAUTH_STATE}"
        ],
    )
