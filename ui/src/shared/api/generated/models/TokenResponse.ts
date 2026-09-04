/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Token endpoint success response.
 */
export type TokenResponse = {
    access_token: string;
    expires_in: number;
    id_token?: (string | null);
    refresh_token?: (string | null);
    /**
     * Space-delimited effective scopes of the minted access token (RFC 6749 §3.3). Always present (RFC 6749 §5.1): the platform downscopes (client ceiling / consent-grant intersection), so the granted set may be narrower than requested and clients must not assume they got what they asked for.
     */
    scope: string;
    token_type?: string;
};

