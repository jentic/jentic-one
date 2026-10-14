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
     * Replacement client secret; omit to leave unchanged. token_url and scopes are applied only together with it.
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
     * Replacement OAuth scopes; applied only when client_secret is sent.
     */
    scopes?: (Array<string> | null);
    /**
     * Replacement OpenAPI server-variable values; omit to leave unchanged.
     */
    server_variables?: (Record<string, string> | null);
    /**
     * Replacement token endpoint URL; applied only when client_secret is sent.
     */
    token_url?: (string | null);
    /**
     * Credential type; always `oauth2`.
     */
    type: string;
};

