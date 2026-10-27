/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Update request for oauth2 credentials.
 */
export type OAuth2UpdateRequest = {
    /**
     * Enable or disable the credential for injection; omit to leave unchanged.
     */
    active?: (boolean | null);
    /**
     * Replacement client secret; omit to leave unchanged. Required when `token_url` moves to a different origin. Rejected with 400 on `device_code` credentials (public client) and on credentials that use a shared app registration.
     */
    client_secret?: (string | null);
    /**
     * New label for the credential; omit to leave unchanged.
     */
    name?: (string | null);
    /**
     * Per-upstream-call overrides; reserved and not currently applied.
     */
    runtime_config?: (RuntimeConfig | null);
    /**
     * Replacement OAuth scopes; omit to leave unchanged, send `[]` to clear them. Each item is a single non-empty scope token without whitespace. Rejected with 400 on credentials that use a shared app registration, and on `device_code` credentials while a device flow is pending.
     */
    scopes?: (Array<string> | null);
    /**
     * Replacement OpenAPI server-variable values; omit to leave unchanged.
     */
    server_variables?: (Record<string, string> | null);
    /**
     * Replacement token endpoint URL; omit to leave unchanged. A change of origin (scheme, host or port) requires `client_secret` in the same request and disconnects the credential until it is reconnected; on `device_code` credentials it is rejected with 400. Rejected with 400 on credentials that use a shared app registration, and on `device_code` credentials while a device flow is pending.
     */
    token_url?: (string | null);
    /**
     * Credential type; always `oauth2`.
     */
    type: string;
};

