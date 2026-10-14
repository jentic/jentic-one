/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReferenceRequest } from './APIReferenceRequest';
import type { RuntimeConfig } from './RuntimeConfig';
/**
 * Create request for oauth2 credentials.
 *
 * For managed providers (e.g. pipedream, direct_oauth2), token_url/client_id/client_secret
 * are optional — the connect flow handles authentication without caller-supplied client details.
 */
export type OAuth2CreateRequest = {
    api: APIReferenceRequest;
    /**
     * Authorization endpoint URL; for the device_code grant, the device authorization endpoint.
     */
    authorize_url?: (string | null);
    /**
     * OAuth client ID; required for non-managed providers.
     */
    client_id?: (string | null);
    /**
     * OAuth client secret; required for non-managed providers except the device_code grant. Stored encrypted; never returned after create.
     */
    client_secret?: (string | null);
    /**
     * OAuth2 grant type (e.g. client_credentials, authorization_code, device_code).
     */
    grant_type?: string;
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
     * OAuth scopes to request.
     */
    scopes?: (Array<string> | null);
    /**
     * OpenAPI server-variable values for URL template substitution (at most 20).
     */
    server_variables?: (Record<string, string> | null);
    /**
     * Token endpoint URL; required for non-managed providers.
     */
    token_url?: (string | null);
    /**
     * Credential type; always `oauth2`.
     */
    type: string;
};

