/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { CredentialLocation } from './CredentialLocation';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for api_key credentials.
 */
export type ApiKeyCreateRequest = {
    /**
     * Loose (vendor, name, version) API identity tuple.
     */
    api: APIReferenceRequest;
    /**
     * Header or query-parameter name carrying the key.
     */
    field_name: string;
    /**
     * The API key secret. Stored encrypted; never returned after create.
     */
    key: string;
    /**
     * Where to inject the key on upstream calls.
     */
    location: CredentialLocation;
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
     * Credential type; always `api_key`.
     */
    type: string;
};

