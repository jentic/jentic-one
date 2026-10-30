/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Access token response after successful authentication.
 */
export type LoginResponse = {
    /**
     * Opaque bearer access token (prefixed `at_`) to send as `Authorization: Bearer <token>` on subsequent requests. It is not a JWT: it is a random string validated server-side by lookup, so it carries no readable claims and its expiry is `expires_in`, not a decodable field.
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

