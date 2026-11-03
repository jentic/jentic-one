/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Update request for no_auth credentials (label, enablement, server variables).
 */
export type NoAuthUpdateRequest = {
    /**
     * Enable or disable the credential for injection; omit to leave unchanged.
     */
    active?: (boolean | null);
    /**
     * New label for the credential; omit to leave unchanged.
     */
    name?: (string | null);
    /**
     * Per-upstream-call overrides; reserved and not currently applied.
     */
    runtime_config?: (RuntimeConfig | null);
    /**
     * Replacement OpenAPI server-variable values; omit to leave unchanged.
     */
    server_variables?: (Record<string, string> | null);
    /**
     * Credential type; always `no_auth`.
     */
    type: string;
};

