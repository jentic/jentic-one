/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * POST /oauth-clients response (201 created, or 200 on a D8 dedupe hit).
 *
 * RFC 7591-compatible: ``client_id`` plus the registered metadata. No
 * ``client_secret`` (public clients only) and no
 * ``registration_access_token`` (D12 — no RFC 7592 self-management surface;
 * clients retry ``/authorize``, they don't poll).
 */
export type OAuthClientRegistrationResponse = {
    application_type?: (string | null);
    client_id: string;
    /**
     * Seconds since the Unix epoch at which the client_id was issued.
     */
    client_id_issued_at: number;
    client_name: string;
    grant_types?: Array<string>;
    redirect_uris: Array<string>;
    response_types?: Array<string>;
    /**
     * Space-separated scope ceiling granted to the client (the request's scope capped to the MCP tool-scope set).
     */
    scope: string;
    software_id?: (string | null);
    software_version?: (string | null);
    token_endpoint_auth_method?: string;
};

