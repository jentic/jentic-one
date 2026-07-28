"""Credential-related enums shared across modules."""

from enum import StrEnum


class StoredCredentialType(StrEnum):
    """Type of stored credential for API authentication."""

    API_KEY = "API_KEY"
    BASIC_AUTH = "BASIC_AUTH"
    STATIC_BEARER_TOKEN = "STATIC_BEARER_TOKEN"
    SESSION_TOKEN = "SESSION_TOKEN"
    OAUTH2_CLIENT_CREDENTIALS = "OAUTH2_CLIENT_CREDENTIALS"
    OAUTH2_AUTHORIZATION_CODE = "OAUTH2_AUTHORIZATION_CODE"
    OAUTH2_IMPLICIT = "OAUTH2_IMPLICIT"
    NO_AUTH = "NO_AUTH"


class CredentialType(StrEnum):
    """Wire-level credential type used by the provider abstraction."""

    BEARER_TOKEN = "bearer_token"
    API_KEY = "api_key"
    BASIC = "basic"
    OAUTH2 = "oauth2"
    NO_AUTH = "no_auth"


class CredentialLocation(StrEnum):
    """Where an API-key credential is injected into a request."""

    HEADER = "header"
    QUERY = "query"
    COOKIE = "cookie"
