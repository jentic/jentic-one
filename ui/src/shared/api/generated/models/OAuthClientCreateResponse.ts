/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Returned on creation — includes the one-time plaintext client secret.
 */
export type OAuthClientCreateResponse = {
    active: boolean;
    /**
     * Scopes this client may request. Null means unrestricted.
     */
    allowed_scopes: (Array<string> | null);
    /**
     * Public client identifier used in OAuth flows.
     */
    client_id: string;
    /**
     * The client secret. Shown only once at creation — store it securely.
     */
    client_secret: string;
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
     * Whether a consent screen is shown during authorization.
     */
    require_consent: boolean;
    updated_at: (string | null);
};

