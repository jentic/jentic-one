/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for basic credentials.
 */
export type BasicAuthCreateRequest = {
    api: APIReferenceRequest;
    /**
     * Human-readable label for the credential.
     */
    name: string;
    /**
     * Password for HTTP Basic auth. Stored encrypted; never returned after create.
     */
    password: string;
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
     * Credential type; always `basic`.
     */
    type: string;
    /**
     * Username sent with HTTP Basic auth.
     */
    username: string;
};

