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
     * Public client identifier used in OAuth flows.
     */
    client_id: string;
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

