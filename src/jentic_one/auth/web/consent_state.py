"""Signed flow blobs and single-use markers of the approval and consent flow.

Every blob here uses the shared signer in :mod:`jentic_one.auth.web.flow`
(:func:`~jentic_one.auth.web.flow.sign_payload` /
:func:`~jentic_one.auth.web.flow.verify_payload`) with its own derived key AND
``_purpose`` discriminator, so no blob can be replayed as another:

- ``approval`` — the approval-pending page's poll + inline-decision key;
- ``agent-create`` — the inline create-agent form, single use, bound to the
  consent handle AND the authenticated subject;
- ``agent-status`` — the pending-agent awaiting page's poll key, repeatable,
  bound to the consent handle AND the agent id.

The consent handle itself (written by ``flow.write_*_consent_handle``) is
read and burned here, alongside the per-blob and per-subject ``set_if_absent``
markers that make the create path single-shot.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time

from fastapi import Request

from jentic_one.auth.core.idp import IdpClaims, parse_email_verified
from jentic_one.auth.web.flow import (
    CONSENT_STATE_MAX_AGE_SECONDS,
    STATE_MAX_AGE_SECONDS,
    agent_create_signing_key,
    agent_status_signing_key,
    approval_state_key,
    get_consent_backend,
    sign_payload,
    verify_payload,
)
from jentic_one.shared.context import Context

#: TTL for the signed approval-state blob carried by the approval-pending page
#: (poll + inline-decision key). Deliberately the same window as the IdP-leg
#: ``state`` — the page self-heals past expiry by re-running /authorize, which
#: mints a fresh blob while the client stays pending.
APPROVAL_STATE_MAX_AGE_SECONDS = STATE_MAX_AGE_SECONDS


def _handle_digest(consent_token: str) -> str:
    return hashlib.sha256(consent_token.encode()).hexdigest()


# ---------- the approval-pending page ----------


def mint_approval_state(
    ctx: Context,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scope: str,
    state: str | None,
    nonce: str | None,
) -> str:
    """Sign the ORIGINAL authorize parameters into the approval-state blob.

    The blob is the only key the anonymous status endpoint accepts — never a
    bare client_id — and its ``iat`` bounds its life to
    :data:`APPROVAL_STATE_MAX_AGE_SECONDS`.
    """
    return sign_payload(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "nonce": nonce,
            "original_state": state,
            "iat": str(int(time.time())),
        },
        approval_state_key(ctx),
        purpose="approval",
    )


def verify_approval_state(ctx: Context, approval_state: str) -> dict[str, str | None]:
    """Verify an approval-state blob; raises ``InvalidGrantError`` on any failure."""
    return verify_payload(
        approval_state,
        approval_state_key(ctx),
        purpose="approval",
        max_age=APPROVAL_STATE_MAX_AGE_SECONDS,
    )


# ---------- consent handle ----------


async def load_consent_handle(ch: str, request: Request) -> dict[str, object] | None:
    """Load consent params for a handle from the shared state backend."""
    backend = get_consent_backend(request)
    raw = await backend.get(f"consent-handle:{ch}")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def consume_consent_handle(ch: str, request: Request) -> bool:
    """Atomically mark a consent handle as used; returns False on replay."""
    backend = get_consent_backend(request)
    return await backend.set_if_absent(
        f"consent-handle-used:{ch}", b"1", ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS)
    )


def claims_from_params(params: dict[str, object]) -> IdpClaims | None:
    """Re-hydrate the IdP claims stored on the consent handle."""
    claims_data = params.get("claims")
    if not isinstance(claims_data, dict):
        return None
    # ``hosted_domain`` must survive the round trip: admission-policy hard gates
    # (e.g. Google Workspace ``hd``) read it, and a handle rebuilt without it
    # rejects every Workspace user. Handles that predate the field read None.
    hosted_domain = claims_data.get("hosted_domain")
    return IdpClaims(
        external_subject=str(claims_data.get("external_subject") or ""),
        email=str(claims_data.get("email") or ""),
        email_verified=parse_email_verified(claims_data.get("email_verified")),
        first_name=str(claims_data.get("first_name") or ""),
        last_name=str(claims_data.get("last_name") or ""),
        hosted_domain=str(hosted_domain) if hosted_domain else None,
        issuer=str(claims_data.get("issuer") or ""),
    )


def consent_user_key(params: dict[str, object]) -> str | None:
    """A stable, non-PII identifier for the human the consent handle authenticated.

    The agent-create blob is bound to this value so a blob minted for one
    subject can never drive a create for another (a mid-window handle rewrite
    or a spliced form must fail closed). Local-login handles pin the
    provisioned ``user_id``; IdP handles pin the external subject (the user
    row may not exist yet — deferred provisioning). ``None`` means the handle
    names no provisionable subject at all, so no create form is offered.
    """
    local_user_id = params.get("local_user_id")
    if local_user_id:
        return f"local:{local_user_id}"
    claims_data = params.get("claims")
    if isinstance(claims_data, dict) and claims_data.get("external_subject"):
        issuer = claims_data.get("issuer")
        if issuer:
            return f"idp:{issuer}:{claims_data['external_subject']}"
        return f"idp:{claims_data['external_subject']}"
    return None


# ---------- inline agent creation ----------


def mint_agent_create_state(ctx: Context, *, consent_token: str, user_key: str) -> str:
    """Sign the agent-create blob: bound to the consent handle AND the subject.

    The handle rides in as a digest (never the raw handle — the blob lives in
    page HTML and must not become a second copy of the capability), plus the
    subject key and ``iat``. Same signer/mechanism as the other flow blobs,
    fifth distinct purpose + derived key (mutual rejection with
    ``state``/``approval``/``login``/``session``).
    """
    return sign_payload(
        {
            "ch_digest": _handle_digest(consent_token),
            "user_key": user_key,
            # Per-mint entropy: without it two mints inside the same second
            # are byte-identical (same payload → same HMAC), so the fresh
            # blob a validation re-render embeds would already be burned.
            "n": secrets.token_urlsafe(8),
            "iat": str(int(time.time())),
        },
        agent_create_signing_key(ctx),
        purpose="agent-create",
    )


def verify_agent_create_state(ctx: Context, create_state: str) -> dict[str, str | None]:
    """Verify an agent-create blob; raises ``InvalidGrantError`` on any failure."""
    return verify_payload(
        create_state,
        agent_create_signing_key(ctx),
        purpose="agent-create",
        max_age=CONSENT_STATE_MAX_AGE_SECONDS,
    )


def agent_create_state_binds(
    blob: dict[str, str | None], *, consent_token: str, user_key: str | None
) -> bool:
    """Whether a verified agent-create blob was minted for this handle AND subject."""
    return (
        blob.get("ch_digest") == _handle_digest(consent_token)
        and user_key is not None
        and blob.get("user_key") == user_key
    )


async def consume_agent_create_state(create_state: str, request: Request) -> bool:
    """Atomically spend the agent-create blob; ``False`` on replay.

    ``set_if_absent`` on a used-marker makes the first submit win and every
    replay inside the TTL lose — a captured form must not keep creating
    agents. A validation re-render mints a FRESH blob, so the burn here never
    strands a user mid-retry.
    """
    backend = get_consent_backend(request)
    digest = hashlib.sha256(create_state.encode()).hexdigest()
    return await backend.set_if_absent(
        f"agent-create-used:{digest}", b"1", ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS)
    )


async def claim_agent_create_slot(user_key: str, request: Request) -> bool:
    """Atomically claim the one inline-create slot per subject; ``False`` = lost.

    Closes the parallel-submit race the per-blob burn cannot (security
    review): N GET renders mint N distinct blobs, and N concurrent submits
    each burn their OWN blob and pass the plain-read ``owner_has_any_agents``
    check before any insert commits — N agents. Same ``set_if_absent`` burn
    pattern, keyed by the SUBJECT instead of the blob, so exactly one submit
    per consenting user ever reaches ``AgentService.create``; losers re-enter
    consent, which by then renders the picker or the awaiting page.
    """
    backend = get_consent_backend(request)
    return await backend.set_if_absent(
        f"agent-create-done:{user_key}", b"1", ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS)
    )


def mint_agent_status_state(ctx: Context, *, consent_token: str, agent_id: str) -> str:
    """Sign the pending-agent status blob: bound to the consent handle AND the agent.

    Minted only after the flow itself verified the agent belongs to the
    handle's subject (at creation, or at the pending re-entry render), so the
    signed ``agent_id`` is the ONLY agent this blob can ever poll — a blob
    can never be pointed at someone else's agent, and the poll response is a
    bare tri-state, so possession reveals nothing an attacker could not
    already see on the page carrying it. Repeatable by design (the page polls
    for minutes), unlike the single-use ``agent-create`` form blob — hence
    the sixth distinct purpose + derived key (mutual rejection with
    ``state``/``approval``/``login``/``session``/``agent-create``).
    """
    return sign_payload(
        {
            "ch_digest": _handle_digest(consent_token),
            "agent_id": agent_id,
            "iat": str(int(time.time())),
        },
        agent_status_signing_key(ctx),
        purpose="agent-status",
    )


def verify_agent_status_state(ctx: Context, status_state: str) -> dict[str, str | None]:
    """Verify an agent-status blob; raises ``InvalidGrantError`` on any failure."""
    return verify_payload(
        status_state,
        agent_status_signing_key(ctx),
        purpose="agent-status",
        max_age=CONSENT_STATE_MAX_AGE_SECONDS,
    )
