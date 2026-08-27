/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for updating an OAuth client.
 */
export type OAuthClientUpdateRequest = {
    active?: (boolean | null);
    /**
     * Scope restriction list. Null means no change; an empty list clears the restriction (all scopes permitted).
     */
    allowed_scopes?: (Array<string> | null);
    description?: (string | null);
    name?: (string | null);
    redirect_uris?: (Array<string> | null);
    require_consent?: (boolean | null);
};

