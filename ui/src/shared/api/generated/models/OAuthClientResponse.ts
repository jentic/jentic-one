/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An OAuth client in API responses.
 */
export type OAuthClientResponse = {
    /**
     * Whether the client is enabled; false for disabled, pending or denied clients.
     */
    active: boolean;
    /**
     * Number of active consent→agent grants for this client. Computed on the read endpoints (list/get); write-path responses report 0.
     */
    active_grant_count?: number;
    /**
     * Scopes this client may request. Null means unrestricted.
     */
    allowed_scopes: (Array<string> | null);
    /**
     * Admin approval lifecycle: ``pending``, ``approved``, or ``denied``. Only approved clients may enter OAuth flows; ``active`` remains the independent kill switch.
     */
    approval_status: string;
    /**
     * Public client identifier used in OAuth flows.
     */
    client_id: string;
    /**
     * What a user's consent grants for this client: ``user`` or ``agent``.
     */
    consent_model: string;
    /**
     * When the client was created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the actor that created the client; null if not recorded.
     */
    created_by: (string | null);
    /**
     * Description of the client; null if none.
     */
    description: (string | null);
    /**
     * Internal ID (ksuid).
     */
    id: string;
    /**
     * Human-readable name for the client.
     */
    name: string;
    /**
     * Allowed OAuth callback URLs.
     */
    redirect_uris: Array<string>;
    /**
     * How the client entered the registry: ``admin`` or ``dcr``.
     */
    registration_source: string;
    /**
     * Whether a consent screen is shown during authorization.
     */
    require_consent: boolean;
    /**
     * RFC 7591 software identifier claimed at registration, if any.
     */
    software_id: (string | null);
    /**
     * Client authentication method at the token endpoint: ``client_secret_basic`` (confidential) or ``none`` (public, PKCE-only).
     */
    token_endpoint_auth_method: string;
    /**
     * When the client was last updated (RFC 3339); null if never updated.
     */
    updated_at: (string | null);
};

