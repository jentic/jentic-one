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
    active?: (boolean | null);
    /**
     * Immutable after create. Accepted for backward compatibility; if provided it must equal the stored value, otherwise the request is rejected. Recreate the credential to change the parameter name.
     */
    field_name?: (string | null);
    key?: (string | null);
    /**
     * Immutable after create. Accepted for backward compatibility; if provided it must equal the stored value, otherwise the request is rejected. Recreate the credential to change the injection binding.
     */
    location?: (CredentialLocation | null);
    name?: (string | null);
    runtime_config?: (RuntimeConfig | null);
    server_variables?: (Record<string, string> | null);
    type: string;
};

