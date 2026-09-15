/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * External-IdP login (the ``GET /auth/idp`` hint, restated).
 */
export type IdpMethodResponse = {
    enabled: boolean;
    /**
     * Provider name when enabled (e.g. 'google'); null when disabled.
     */
    provider: (string | null);
};

