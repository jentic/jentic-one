"""Credential header ORM model for tracking API authentication credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant

if TYPE_CHECKING:
    from jentic_one.control.core.schema.basic_credentials import BasicCredential
    from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
    from jentic_one.control.core.schema.device_authorization_credentials import (
        DeviceAuthorizationCredential,
    )
    from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
    from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
    from jentic_one.control.core.schema.oauth_tokens import OAuthToken
    from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
    from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential


class Credential(AuditableMixin, ControlBase):
    """Polymorphic header table for stored API credentials."""

    __tablename__ = "credentials"
    __table_args__ = (
        Index("ix_credentials_api_vendor", "api_vendor"),
        Index("ix_credentials_provider", "provider"),
        Index("ix_credentials_owner_user_id", "owner_user_id"),
        Index("ix_credentials_oauth_app_registration_id", "oauth_app_registration_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cred"),
        server_default=func.generate_ksuid("cred"),
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_vendor: Mapped[str] = mapped_column(String(100), nullable=False)
    api_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Match apis.version (String(100)); the registry does not length-cap versions
    # and SNAPSHOT builds carry a commit suffix that overflows 50 chars (#690).
    api_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Catalog identity slug of the target API (`domain[/sub-api]`), copied at
    # create time when the client knows it. Display-only — resolution identity
    # stays the (api_vendor, api_name, api_version) tuple. NULL for credentials
    # created before this column or against manually-imported APIs.
    catalog_api_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="static", server_default=text("'static'")
    )
    provider_account_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Lifecycle state — introduced by the agent-driven integration flow.
    # `pending` = credential row created upfront by /integrations:connect,
    # awaiting device-flow (or other) completion. `connected` = live, usable
    # by the broker. `failed` = terminal failure of the connect session.
    # Broker credential resolution must skip non-`connected` rows.
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="connected", server_default=text("'connected'")
    )
    server_variables: Mapped[dict[str, str] | None] = mapped_column(
        json_variant(), nullable=True, default=None
    )
    # NULL = org-shared (anyone on the instance may resolve this credential);
    # non-NULL = only that user (and their on-behalf-of agents whose
    # ``parent_actor_id`` matches) may resolve. Enforced inside the broker's
    # binding boundary — the FK-less user id references the auth surface.
    owner_user_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Set on OAuth grants minted through a shared registration. NULL when the
    # legacy embedded ``oauth_client_credentials`` path is used, and on
    # non-OAuth credential types.
    oauth_app_registration_id: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("oauth_app_registrations.id", ondelete="RESTRICT"),
        nullable=True,
    )

    customer_api_key: Mapped[CustomerAPIKey | None] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    oauth_client_credential: Mapped[OAuthClientCredential | None] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    basic_credential: Mapped[BasicCredential | None] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    token_value_credential: Mapped[TokenValueCredential | None] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    oauth_token: Mapped[OAuthToken | None] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    sigv4_credential: Mapped[Sigv4Credential | None] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    device_authorization_credential: Mapped[DeviceAuthorizationCredential | None] = relationship(
        back_populates="credential",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    oauth_app_registration: Mapped[OAuthAppRegistration | None] = relationship(
        back_populates="credentials",
        lazy="selectin",
    )
