/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for bearer_token credentials.
 */
export type BearerTokenCreateRequest = {
    api: APIReferenceRequest;
    /**
     * Human-readable label for the credential.
     */
    name: string;
    /**
     * Credential provider; 'static' for stored secrets.
     */
    provider?: string;
    /**
     * Per-upstream-call overrides; reserved and not currently applied.
     */
    runtime_config?: (RuntimeConfig | null);
    /**
     * OpenAPI server-variable values for URL template substitution (at most 20).
     */
    server_variables?: (Record<string, string> | null);
    /**
     * The bearer token secret. Stored encrypted; never returned after create.
     */
    token: string;
    /**
     * Credential type; always `bearer_token`.
     */
    type: string;
};

