"""Admin surface configuration: login, invites, and events."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, model_validator

from jentic_one.shared.config._secrets import _require_or_generate_secret


class AdminAuthConfig(BaseModel):
    """Admin authentication settings."""

    jwt_secret: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "HMAC secret that signs admin login JWTs. Required in production "
            "(empty or placeholder values are rejected at startup); in "
            "development an ephemeral per-process secret is generated when empty."
        ),
    )
    jwt_ttl_seconds: int = Field(
        default=3600,
        gt=0,
        description=(
            "Lifetime of one admin login JWT. ``POST /auth/refresh`` re-mints "
            "the token, so this bounds a single token, not the session."
        ),
    )
    session_ttl_seconds: int = Field(
        default=43200,
        gt=0,
        description=(
            "Absolute cap on a web session: refresh re-mints the login JWT only "
            "while ``now - auth_time`` stays inside this window, so a leaked "
            "token cannot be kept alive indefinitely. Must be >= "
            "``jwt_ttl_seconds``."
        ),
    )
    failed_login_lockout_threshold: int = Field(
        default=5,
        description=("Consecutive failed logins after which an admin account is locked."),
    )
    failed_login_lockout_seconds: int = Field(
        default=900,
        description=(
            "How long a locked admin account stays locked before logins are accepted again."
        ),
    )

    @model_validator(mode="after")
    def _require_or_generate_secret_in_dev(self) -> AdminAuthConfig:
        self.jwt_secret = _require_or_generate_secret(
            self.jwt_secret, field_path="admin.auth.jwt_secret"
        )
        return self

    @model_validator(mode="after")
    def _session_window_covers_jwt_ttl(self) -> AdminAuthConfig:
        # A session window shorter than one JWT would make every refresh fail
        # while the first token is still valid — always a misconfiguration.
        if self.session_ttl_seconds < self.jwt_ttl_seconds:
            raise ValueError("admin.auth.session_ttl_seconds must be >= admin.auth.jwt_ttl_seconds")
        return self


class AdminInviteConfig(BaseModel):
    """Admin invite token settings."""

    ttl_days: int = 7
    pepper: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def _require_or_generate_secret_in_dev(self) -> AdminInviteConfig:
        self.pepper = _require_or_generate_secret(self.pepper, field_path="admin.invite.pepper")
        return self


class AdminEventsConfig(BaseModel):
    """Admin event-stream settings."""

    stream_recheck_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Minimum seconds between re-verifications of an open "
            "``GET /events/stream``: its credential, ``events:read`` / "
            "``org:admin`` permission, and the scope of the events it delivers. "
            "Checks run only at a poll or page boundary, so a check happens at "
            "the first poll or page after the interval elapses; an idle stream "
            "polls every few seconds, so a value below its poll interval behaves "
            "as that interval. A disabled caller, a revoked or expired token, or "
            "a revoked permission ends the stream at that check; a narrower "
            "permission set narrows it. Permissions are those the credential "
            "carries: API keys and user ``at_`` access tokens resolve them live, "
            "while a login session JWT and an agent ``at_`` access token carry "
            "the set they were issued with; the stream re-verifies the token it "
            "was opened with, so for those a change applies once that token "
            "expires and the client reconnects."
        ),
    )


class AdminConfig(BaseModel):
    """Admin surface configuration."""

    auth: AdminAuthConfig = Field(default_factory=AdminAuthConfig)
    invite: AdminInviteConfig = Field(default_factory=AdminInviteConfig)
    events: AdminEventsConfig = Field(default_factory=AdminEventsConfig)
