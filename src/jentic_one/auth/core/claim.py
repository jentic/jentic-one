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
"""

from __future__ import annotations

from typing import Protocol


class ClaimTokenMinter(Protocol):
    """Mints the opaque claim token returned from ``POST /register`` (or None)."""

    def __call__(self, agent_id: str) -> str | None:
        """Return an opaque, single-use claim token for the freshly-created agent,
        or ``None`` to mint no token (the OSS default — no claim flow)."""
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
