/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An OAuth client in API responses.
 */
export type OAuthClientResponse = {
    active: boolean;
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
    created_at: string;
    created_by: (string | null);
    description: (string | null);
    /**
     * Internal ID (ksuid).
     */
    id: string;
    name: string;
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
    updated_at: (string | null);
};

