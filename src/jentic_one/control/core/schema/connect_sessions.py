"""Connect session ORM model — the flow-agnostic pending-session record.

Owns the state machine that drives the agent-driven integration flow.
Flow-specific transient state lives on auxiliary tables keyed by
`credential_id` (e.g. `device_authorization_credentials`), NOT here.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant

#: Target kinds a session can carry. ``vendor`` is a vendor-registry key (an
#: OAuth app resolves from it); ``api`` is a registry API identity
#: ``(vendor, api_name, api_version)``.
TARGET_KIND_VENDOR = "vendor"
TARGET_KIND_API = "api"

# One open session per agent and API identity. Mirrors the repository's live
# states; kept as a literal so the index predicate is plain, portable SQL.
_OPEN_API_TARGET_PREDICATE = (
    "target_kind = 'api' AND state IN ('created', 'awaiting_app', 'polling') "
    "AND agent_id IS NOT NULL"
)

# One open agent-started session per agent, vendor key and OAuth request
# (``dedupe_key``: resolved flow, registration and requested scopes). Only
# agent-started ``vendor`` sessions carry a key; every other row is NULL and
# outside the index.
_OPEN_VENDOR_TARGET_PREDICATE = (
    "target_kind = 'vendor' AND state IN ('created', 'awaiting_app', 'polling') "
    "AND agent_id IS NOT NULL AND dedupe_key IS NOT NULL"
)


class ConnectSession(AuditableMixin, ControlBase):
    """A pending (or completed) connect session, keyed on the target credential."""

    __tablename__ = "connect_sessions"
    __table_args__ = (
        Index("ix_connect_sessions_agent", "agent_id", "state"),
        Index("ix_connect_sessions_credential", "credential_id"),
        # Keyed by the DB column name (``poll_token``); the ORM attribute is
        # ``poll_token_hash`` — see the column comment below.
        Index("ix_connect_sessions_poll_token", "poll_token", unique=True),
        # Serves the admin-console list (GET /connect-sessions): filter by
        # state + keyset pagination on created_at.
        Index("ix_connect_sessions_state_created_at", "state", "created_at"),
        Index(
            "ix_connect_sessions_open_api_target",
            "agent_id",
            "vendor",
            "api_name",
            "api_version",
            unique=True,
            postgresql_where=text(_OPEN_API_TARGET_PREDICATE),
            sqlite_where=text(_OPEN_API_TARGET_PREDICATE),
        ),
        Index(
            "ix_connect_sessions_open_vendor_target",
            "agent_id",
            "vendor",
            "dedupe_key",
            unique=True,
            postgresql_where=text(_OPEN_VENDOR_TARGET_PREDICATE),
            sqlite_where=text(_OPEN_VENDOR_TARGET_PREDICATE),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cs"),
        server_default=func.generate_ksuid("cs"),
    )
    # Target credential (state=pending until the flow completes).
    credential_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ``vendor``: ``vendor`` is a vendor-registry key. ``api``: the session
    # targets a registry API identity — ``vendor`` is the API vendor and
    # ``api_name`` / ``api_version`` are set. The server default keeps every
    # row written without the column a vendor target.
    target_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TARGET_KIND_VENDOR, server_default=TARGET_KIND_VENDOR
    )
    # Vendor registry key (e.g. "github") for a ``vendor`` target; the API
    # vendor (e.g. "github-com") for an ``api`` target. FK-less; the registry
    # is config-seeded.
    vendor: Mapped[str] = mapped_column(String(255), nullable=False)
    # API identity of an ``api`` target (both set, version resolved to the
    # live revision); NULL for ``vendor`` targets.
    api_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Snapshot of the declared security scheme an ``api`` target resolved to
    # at create (type, location, header / query / cookie name), so confirm can
    # detect a spec change under the approver. NULL for ``vendor`` targets.
    scheme_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scheme_location: Mapped[str | None] = mapped_column(String(20), nullable=True)
    scheme_field_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Snapshot of the API's canonical hosts at create, for the same check.
    pinned_hosts: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    # For an ``api`` target whose OAuth app resolved from the vendor registry:
    # the registry key it resolved to (config entry key, or the shared app's
    # vendor slug). Every vendor-registry read on such a session uses this key.
    # NULL for ``vendor`` targets (``vendor`` is the key) and for ``api``
    # targets without a registry app (human-entered credential, own client).
    vendor_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Digest of what an agent-started ``vendor`` session asks for (flow,
    # OAuth app registration, requested scopes), keying the open-session
    # dedupe index. NULL on every other row.
    dedupe_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Target agent to bind on success. FK-less: `agents` lives in the admin DB
    # (cross-DB FKs are forbidden by the architecture).
    # Nullable: present when the initiator is an agent (from the auth
    # identity) or when a user caller supplied it — confirm then creates
    # the direct agent-credential binding + permission rules. None means
    # the credential connects unbound (a user can bind an agent later
    # through the credentials API).
    agent_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Who called `:connect`. Actor type is derived from the id prefix
    # (`agt_`/`usr_`/`sa_`) via the existing identity utility — no separate col.
    initiator_actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    # State machine: created | awaiting_app | polling | connected | expired | failed
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    # As-requested by initiator (may differ from resolved).
    preferred_flow: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Actually used for the connect. Selects which aux table holds flow state.
    resolved_flow: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # As-requested scope list from the initiator. Session-scoped state (not
    # flow-specific), so it lives on the session row rather than an aux
    # table — that keeps ``get_review_data`` flow-agnostic. Nullable to
    # allow older rows created before this column landed.
    requested_scopes: Mapped[list[str] | None] = mapped_column(
        json_variant(), nullable=True, default=list
    )
    # As-requested permission rules from the initiator (typically an agent
    # asking the human owner to approve them). Stored on the session — not
    # written to ``agent_permission_rules`` at ``:connect`` time — because
    # they are the initiator's *request*, only persisted onto the binding
    # once the human confirms or edits them on the review page. Each
    # element is an ``AgentPermissionRule`` dict per ``PermissionRuleSchema``:
    # ``{effect, methods, path, match_mode, operations, comment}``.
    requested_permission_rules: Mapped[list[dict[str, object]]] = mapped_column(
        json_variant(), nullable=False, default=list, server_default="[]"
    )
    # Identity echo result (e.g. "@octocat"); set on `connected`.
    connected_as: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Terminal failure code (see `error-taxonomy` — machine-readable slug).
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # SHA-256 hex digest of the opaque poll token given to the initiator so
    # it can call /connect-sessions/{id}/status without full session-read
    # auth. The plaintext is returned once from ``:connect`` and never
    # stored; verification hashes the presented token (``hash_secret``).
    # The DB column keeps its original name ``poll_token`` so a previous
    # release still serving during a rolling upgrade can load rows (a rename
    # would make every connect-session query fail on those pods); only the
    # stored value changed shape (migration e1a2b3c4d5f6).
    poll_token_hash: Mapped[str] = mapped_column("poll_token", String(64), nullable=False)
    # Optional free-text detail useful for human debugging on the review page.
    # (Deliberately excluded from the plan's `connect_sessions`; kept here as a
    # nullable audit column with no operational meaning — logs remain the
    # authoritative source of failure detail.)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PKCE (RFC 7636) verifier for the auth-code flow. Generated at ``begin``
    # and echoed back to the vendor at ``complete_from_callback``. Persisted
    # server-side (never in the state JWT, which transits the browser).
    # Nullable: device flow never sets it, and existing in-flight sessions
    # created before this column existed will complete without PKCE.
    pkce_code_verifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
