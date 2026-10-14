/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Update request for sigv4 credentials (key rotation / scope edit).
 */
export type Sigv4UpdateRequest = {
    /**
     * Replacement AWS access key ID; must be sent with secret_access_key.
     */
    access_key_id?: (string | null);
    /**
     * Enable or disable the credential for injection; omit to leave unchanged.
     */
    active?: (boolean | null);
    /**
     * Replacement signing region; omit to leave unchanged.
     */
    aws_region?: (string | null);
    /**
     * Replacement signing service; omit to leave unchanged.
     */
    aws_service?: (string | null);
    /**
     * Remove the stored session token; takes precedence over session_token.
     */
    clear_session_token?: boolean;
    /**
     * New label for the credential; omit to leave unchanged.
     */
    name?: (string | null);
    /**
     * Per-upstream-call overrides; reserved and not currently applied.
     */
    runtime_config?: (RuntimeConfig | null);
    /**
     * Replacement AWS secret access key; must be sent with access_key_id.
     */
    secret_access_key?: (string | null);
    /**
     * Replacement OpenAPI server-variable values; omit to leave unchanged.
     */
    server_variables?: (Record<string, string> | null);
    /**
     * Replacement STS session token; omit to leave unchanged.
     */
    session_token?: (string | null);
    /**
     * Credential type; always `sigv4`.
     */
    type: string;
};

