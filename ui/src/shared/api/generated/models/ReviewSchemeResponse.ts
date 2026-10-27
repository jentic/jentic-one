/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The declared scheme an API target collects (snapshot from its spec).
 */
export type ReviewSchemeResponse = {
    /**
     * An OAuth scheme's authorize URL; set only for a public-catalog spec.
     */
    authorization_url?: (string | null);
    /**
     * Header, query or cookie name an API key is sent in.
     */
    field_name?: (string | null);
    /**
     * Where an API key goes: `header`, `query` or `cookie`.
     */
    location?: (string | null);
    /**
     * An OAuth scheme's token URL; set only for a public-catalog spec.
     */
    token_url?: (string | null);
    /**
     * The kind of secret the scheme collects.
     */
    type: ReviewSchemeResponse.type;
};
export namespace ReviewSchemeResponse {
    /**
     * The kind of secret the scheme collects.
     */
    export enum type {
        API_KEY = 'api_key',
        BEARER = 'bearer',
        BASIC = 'basic',
        OAUTH2 = 'oauth2',
    }
}

