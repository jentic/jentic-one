/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialLocation } from './CredentialLocation';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Update request for api_key credentials.
 */
export type ApiKeyUpdateRequest = {
    /**
     * Enable or disable the credential for injection; omit to leave unchanged.
     */
    active?: (boolean | null);
    /**
     * Immutable after create. Accepted for backward compatibility; if provided it must equal the stored value, otherwise the request is rejected. Recreate the credential to change the parameter name.
     */
    field_name?: (string | null);
    /**
     * Replacement API key; omit to leave unchanged.
     */
    key?: (string | null);
    /**
     * Immutable after create. Accepted for backward compatibility; if provided it must equal the stored value, otherwise the request is rejected. Recreate the credential to change the injection binding.
     */
    location?: (CredentialLocation | null);
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
     * Credential type; always `api_key`.
     */
    type: string;
};

