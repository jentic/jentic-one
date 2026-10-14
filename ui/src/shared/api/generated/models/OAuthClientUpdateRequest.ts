/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for updating an OAuth client.
 */
export type OAuthClientUpdateRequest = {
    /**
     * False disables the client and true re-enables it; null means no change.
     */
    active?: (boolean | null);
    /**
     * Scope restriction list. Null means no change; an empty list denies all non-OIDC scopes; the wildcard ``["*"]`` resets to unrestricted.
     */
    allowed_scopes?: (Array<string> | null);
    /**
     * New description for the client; null means no change.
     */
    description?: (string | null);
    /**
     * New name for the client; null means no change.
     */
    name?: (string | null);
    /**
     * Replacement list of allowed OAuth callback URLs; null means no change.
     */
    redirect_uris?: (Array<string> | null);
    /**
     * Whether to show a consent screen during authorization; null means no change.
     */
    require_consent?: (boolean | null);
};

