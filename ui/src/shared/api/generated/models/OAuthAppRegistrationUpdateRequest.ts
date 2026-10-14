/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Partial update — fields not present are left untouched.
 */
export type OAuthAppRegistrationUpdateRequest = {
    /**
     * New device authorization endpoint URL; device-authorization registrations only.
     */
    authorization_endpoint?: (string | null);
    /**
     * New authorization endpoint URL; authorization-code registrations only.
     */
    authorize_url?: (string | null);
    /**
     * Replacement default scopes; omit to leave unchanged.
     */
    default_scopes?: (Array<string> | null);
    /**
     * New vendor family label; omit to leave unchanged.
     */
    display_name?: (string | null);
    /**
     * Activate or deactivate the registration; omit to leave unchanged.
     */
    is_active?: (boolean | null);
    /**
     * New registration name; omit to leave unchanged.
     */
    name?: (string | null);
    /**
     * New token endpoint URL; device-authorization registrations only.
     */
    token_endpoint?: (string | null);
    /**
     * New token endpoint URL; authorization-code registrations only.
     */
    token_url?: (string | null);
};

