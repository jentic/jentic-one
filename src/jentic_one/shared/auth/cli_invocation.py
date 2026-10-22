"""CLI invocation tokens: the short-lived credential a vendor CLI uses at a broker facade.

The auth surface mints one per ``jentic gh``/``cf``/``aws`` command, on behalf
of an agent authenticated with its own ``at_`` access token. The token carries
a distinct ``cit_`` prefix so every platform resolver, which accepts only
``at_``/``jak_``/JWT shapes, rejects it by construction: only the broker's
dedicated CLI-facade dependency resolves it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jentic_one.shared.auth.identity import Identity

CLI_INVOCATION_TOKEN_PREFIX = "cit_"


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a token: how ``access_tokens`` and ``cli_invocations`` store it."""
    return hashlib.sha256(token.encode()).hexdigest()


class CliInvocationClaims(BaseModel):
    """What a resolved invocation token says about the command it was minted for.

    ``command_title`` is reported by the agent (the subcommand path only, never
    arguments) and is display-only. ``credential_name`` picks among the
    agent's credentials for the vendor the way the ``Jentic-Credential-Name``
    header does; ``None`` leaves the choice to the broker's normal selection.
    ``trace_id`` is chosen by the server at
    mint, so a facade groups the command's calls under it without trusting any
    client-supplied trace context.
    """

    model_config = ConfigDict(frozen=True)

    invocation_id: str
    vendor: str
    credential_name: str | None = None
    trace_id: str
    command_title: str | None = None
    expires_at: datetime
    hard_expires_at: datetime


class CliInvocationIdentity(BaseModel):
    """A resolved invocation token: the minting agent's live identity plus the claims.

    ``identity`` is resolved through the parent ``at_`` token on every cache
    miss, so the agent's status, its owner's status, OAuth client and grant
    gates and live permissions all apply, and revoking the parent ends the
    child.
    """

    model_config = ConfigDict(frozen=True)

    identity: Identity
    claims: CliInvocationClaims


__all__ = [
    "CLI_INVOCATION_TOKEN_PREFIX",
    "CliInvocationClaims",
    "CliInvocationIdentity",
    "hash_token",
]
