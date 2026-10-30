/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The declared scheme an API target collects (snapshot from its spec).
 */
export type ReviewSchemeResponse = {
    authorization_url?: (string | null);
    field_name?: (string | null);
    location?: (string | null);
    token_url?: (string | null);
    type: ReviewSchemeResponse.type;
};
export namespace ReviewSchemeResponse {
    export enum type {
        API_KEY = 'api_key',
        BEARER = 'bearer',
        BASIC = 'basic',
        OAUTH2 = 'oauth2',
    }
}

