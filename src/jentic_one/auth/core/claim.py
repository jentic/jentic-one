"""Claim-token minting seam for self-registered (DCR) agents.

``POST /register`` is anonymous by design (RFC 7591): the agent proves nothing
about *who* is registering it, so the agent is created with ``owner_id = NULL``.
For a single-user OSS deployment that is correct — there is no other human to
attribute to. In a **multi-user** deployment the registering human needs a way to
later assert ownership of that pending agent; the missing piece is a *binding
signal* minted at register time.

This module is the injectable seam for that signal. On registration, OSS asks the
installed **claim-token minter** for an opaque token; if one is returned, OSS
stores its hash + expiry on the agent row and echoes the plaintext once on the
register response. The human presents that token to ``POST /agents/{id}:claim``
to set ``owner_id`` to themselves (see ``auth/web/routers/agents.py``).

The default mints **nothing** — the register response and stored row are then
byte-for-byte what they are today, so OSS single-user behaviour is unchanged. A
deployment that wants agent-ownership claiming (e.g. the enterprise overlay)
installs its own minter via :func:`set_claim_token_minter`.

Same process-global registry posture as ``set_admission_policy`` in
``auth/core/idp/provisioning.py``: a module-level default, replaceable at import
time.

Threat model: the claim token is a bearer capability — whoever presents a valid,
unexpired token can set ``owner_id`` to *themselves* (any authenticated user).
The mitigations that keep that acceptable are: the token is single-use (consumed
on the first successful claim), short-lived (``auth.claim_ttl_seconds``, 15m by
default), high-entropy (see the minter contract below), and grants *only*
ownership — it confers no scopes and cannot act as the agent — and ownership
stays re-assignable by an operator via approve. A leaked token therefore lets an
attacker mis-attribute a *pending, unapproved* agent to themselves; an operator
can still re-approve to the correct human. Minters must not log or persist the
plaintext token.
"""

from __future__ import annotations

from typing import Protocol


class ClaimTokenMinter(Protocol):
    """Mints the opaque claim token returned from ``POST /register`` (or None)."""

    def __call__(self, agent_id: str) -> str | None:
        """Return an opaque, single-use claim token for the freshly-created agent,
        or ``None`` to mint no token (the OSS default — no claim flow).

        The token **must be cryptographically random with at least 128 bits of
        entropy** (e.g. ``secrets.token_urlsafe(32)``). OSS stores only an
        unsalted SHA-256 of the token and compares in constant time; a
        low-entropy or guessable token would be brute-forceable offline if the
        hash ever leaked, letting an attacker claim the agent. Entropy is the
        minter's responsibility — OSS does not stretch or salt it.

        ``agent_id`` is the id of the row being created. It is provided for
        context (logging, per-agent policy) but should be treated as **advisory**:
        OSS retries the registration transaction on transient write contention,
        and each attempt creates a new agent id, so a minter that *embeds* the id
        in the token (e.g. a signed token) could bake in a stale one. OSS re-mints
        per attempt and always returns the token whose hash was stored on the row
        that committed, so a stateless random token is unaffected either way.
        """
        ...


def no_claim_token(agent_id: str) -> str | None:
    """Default minter: no claim token.

    Preserves historical OSS behaviour exactly — ``/register`` returns no claim
    token and the agent stays ``owner_id=NULL`` until an operator approves it. A
    deployment that wants the registering human to claim ownership installs its
    own minter via :func:`set_claim_token_minter`.
    """
    return None


_claim_token_minter: ClaimTokenMinter = no_claim_token


def set_claim_token_minter(minter: ClaimTokenMinter) -> None:
    """Install the process-wide claim-token minter for agent self-registration.

    Called once at import/boot by a deployment that wants self-registered agents
    to be claimable by the registering human. Last write wins.
    """
    global _claim_token_minter
    _claim_token_minter = minter


def get_claim_token_minter() -> ClaimTokenMinter:
    """Return the installed claim-token minter (mints nothing by default)."""
    return _claim_token_minter
