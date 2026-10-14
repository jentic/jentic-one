/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * JWT token response after successful authentication.
 */
export type LoginResponse = {
    /**
     * Signed JWT to send as a bearer token on subsequent requests.
     */
    access_token: string;
    /**
     * Lifetime of the access token in seconds.
     */
    expires_in: number;
    /**
     * Whether the user must change their password before using other endpoints.
     */
    must_change_password: boolean;
    /**
     * Token type; always `bearer`.
     */
    token_type: string;
};

