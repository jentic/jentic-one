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
     * After saving, make one read call with the credential and return the verdict as `check`. The credential is saved either way.
     */
    check?: boolean;
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
    runtime_config?: (RuntimeConfig | null);
    server_variables?: (Record<string, string> | null);
    type: string;
};

