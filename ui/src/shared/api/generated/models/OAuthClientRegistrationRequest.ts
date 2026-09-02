/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * POST /oauth-clients request body (RFC 7591 client metadata subset).
 */
export type OAuthClientRegistrationRequest = {
    /**
     * Accepted and echoed ('native' for desktop/CLI apps, per the 2026-07-28 MCP spec revision); localhost http redirect URIs are allowed regardless.
     */
    application_type?: (string | null);
    client_name: string;
    /**
     * Subset of ['authorization_code', 'refresh_token'].
     */
    grant_types?: (Array<string> | null);
    redirect_uris: Array<string>;
    /**
     * Only ['code'] is supported.
     */
    response_types?: (Array<string> | null);
    scope?: (string | null);
    software_id?: (string | null);
    software_version?: (string | null);
    /**
     * Must be 'none' if supplied — this endpoint only registers public (secret-less, PKCE-only) clients.
     */
    token_endpoint_auth_method?: (string | null);
};

