"""Protocol + shared value objects for connect-session flow handlers.

The connect-session state machine + finalise / identity-echo / catalog
auto-import bits live on ``ConnectSessionService`` and are flow-agnostic.
Everything a *particular* auth flow needs to do — set up storage, talk to
the vendor, report progress, cleanup on success — is delegated through
``AuthFlowHandler``. Callback-only concerns (``complete_from_callback``)
live on the concrete handler that has them; the base Protocol stays free
of dummy stubs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.shared.config import VendorFlowConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import StoredCredentialType

# ---------------------------------------------------------------------------
# Begin-result — discriminated union on ``kind`` so callers branch cleanly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceFlowChallenge:
    """RFC 8628 challenge — the user types ``user_code`` at ``verification_uri``."""

    kind: Literal["device_flow"] = "device_flow"
    user_code: str = ""
    verification_uri: str = ""
    verification_uri_complete: str | None = None
    poll_interval_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class AuthCodeChallenge:
    """OAuth 2.0 authorization-code challenge — browser redirect target."""

    kind: Literal["authorization_code"] = "authorization_code"
    authorize_url: str = ""


BeginResult = DeviceFlowChallenge | AuthCodeChallenge


# ---------------------------------------------------------------------------
# Status — the single "how's it going?" answer, uniform across flows.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SuccessTokens:
    """Tokens returned by a successful vendor exchange.

    ``granted_scopes`` is the flow-agnostic "what did the human end up
    with" list — device flow reports the confirmed scope set (some vendors
    return an empty ``scope`` on the token response), auth-code reports
    what the OAuth server actually granted. The service writes it verbatim
    onto ``oauth_token.scope`` so terminal readback works through one
    flow-agnostic column.
    """

    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scope: str | None
    granted_scopes: list[str] | None = None


StatusKind = Literal["pending", "failed", "expired", "success"]


@dataclass(frozen=True, slots=True)
class StatusReport:
    """One ``status`` call's answer.

    The service maps this to its wire ``StatusResult``. Kept off the wire
    types so ``base.py`` never has to import from the service (which would
    create a cycle).

    * ``kind == "pending"`` — no state change; the vendor hasn't finished
      approving yet. Service reports ``StatusResult(status="pending")``.
    * ``kind == "failed"`` / ``"expired"`` — terminal. Service persists via
      ``_mark_terminal`` with ``error_code`` + ``terminal_detail``.
    * ``kind == "success"`` — vendor returned tokens; the service runs its
      finalise sequence with ``tokens``.
    """

    kind: StatusKind
    error_code: str | None = None
    terminal_detail: str | None = None
    tokens: SuccessTokens | None = None


# ---------------------------------------------------------------------------
# Handler protocol
# ---------------------------------------------------------------------------


class AuthFlowHandler(Protocol):
    """One auth flow's contract with the connect-session state machine.

    Lifecycle points:

    * ``prepare`` — create_session txn: set up whatever storage this flow
      needs to remember about the session (aux row, etc.). Caller doesn't
      know about "aux tables".
    * ``begin`` — confirm: talk to the vendor and return the challenge.
    * ``on_finalise`` — inside the finalise txn: cleanup any flow-specific
      transient state (device flow clears device_code; auth-code no-op).

    Progress observation lives on the service: ``get_status`` is a pure
    stored-state read for *every* flow. Vendor advancement is
    scanner-driven for polling flows (``DeviceFlowHandler.advance``, called
    only by ``ConnectPollScanner``) and callback-driven for redirect flows
    (``AuthCodeFlowHandler.complete_from_callback``, called only by the
    callback router). Neither is on this Protocol — a Protocol lie is
    worse than a small concrete-type check at the call site.
    """

    #: Discriminator matching ``VendorFlowConfig.kind``.
    kind: ClassVar[str]
    #: Stored credential type persisted on the credential row.
    stored_type: ClassVar[StoredCredentialType]
    #: ``credential.provider`` value.
    provider_id: ClassVar[str]

    def __init__(self, ctx: Context) -> None: ...

    async def prepare(
        self,
        db_session: AsyncSession,
        *,
        credential_id: str,
        flow: VendorFlowConfig,
        requested_scopes: list[str],
        created_by: str,
    ) -> None:
        """Set up flow-specific storage for the session. In-txn caller-side."""

    async def begin(
        self,
        row: ConnectSession,
        *,
        flow: VendorFlowConfig,
        confirmed_scopes: list[str],
    ) -> BeginResult:
        """Start the vendor conversation. Returns the challenge for the user."""

    async def on_finalise(
        self,
        db_session: AsyncSession,
        *,
        credential_id: str,
    ) -> None:
        """Optional flow-specific cleanup inside the finalise txn.

        Device flow uses this to clear its transient device_code /
        user_code columns. Auth-code has no per-session transient state
        and is a no-op. Flow-agnostic annotations (``granted_scopes`` →
        ``oauth_token.scope``) are written by the service, not here.
        """
