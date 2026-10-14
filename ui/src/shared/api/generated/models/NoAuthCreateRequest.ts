/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for no_auth credentials.
 *
 * A no-auth credential carries no secret — it represents "this API is called
 * without authentication". It still exists as a credential row so an agent
 * binding (and its permission rules) can hang off it, and the broker resolves
 * it as a no-op auth (see broker credential resolver / injection).
 */
export type NoAuthCreateRequest = {
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
     * Credential type; always `no_auth`.
     */
    type: string;
};

