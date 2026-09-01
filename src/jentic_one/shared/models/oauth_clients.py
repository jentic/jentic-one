"""OAuth-client-registry enums shared across modules.

Phase-3a (D5/D6/D7): the ``oauth_clients`` registry stores these as plain
strings (matching the agents-table pattern); the enums are the shared
vocabulary used by services, web schemas, and token verification.
"""

from enum import StrEnum


class TokenEndpointAuthMethod(StrEnum):
    """RFC 7591 ``token_endpoint_auth_method`` values supported by the registry.

    ``client_secret_basic`` clients are confidential (argon2id-hashed secret
    stored); ``none`` clients are public (no secret, PKCE-only — D5).
    """

    CLIENT_SECRET_BASIC = "client_secret_basic"
    NONE = "none"


class OAuthConsentModel(StrEnum):
    """What a user's consent grants for this client (D6).

    ``user``: today's act-as-user semantics (admin-created default).
    ``agent``: consent binds the connection to one of the user's agents
    (the MCP path; enforced from 3a-3 onwards).
    """

    USER = "user"
    AGENT = "agent"


class OAuthRegistrationSource(StrEnum):
    """How an OAuth client row entered the registry."""

    ADMIN = "admin"
    DCR = "dcr"


class OAuthClientApprovalStatus(StrEnum):
    """Admin approval lifecycle for OAuth clients (D7).

    Independent of the ``active`` kill switch: ``pending``/``denied`` rows
    must never pass /authorize validation or mint tokens, while ``active``
    remains the admin's deactivation lever for approved clients. Deny is
    reversible — rows are never deleted.
    """

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
