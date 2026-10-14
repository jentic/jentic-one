/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * RFC 7662 introspection response.
 */
export type IntrospectResponse = {
    /**
     * Whether the token is currently valid.
     */
    active: boolean;
    /**
     * Token expiry, in seconds since the Unix epoch; omitted if unknown.
     */
    exp?: (number | null);
    /**
     * Space-separated effective scopes of the token; omitted if unknown.
     */
    scope?: (string | null);
    /**
     * ID of the actor the token was issued to; omitted if unknown.
     */
    sub?: (string | null);
    /**
     * Kind of token: `access_token` or `refresh_token`; omitted if unknown.
     */
    token_type?: (string | null);
};

