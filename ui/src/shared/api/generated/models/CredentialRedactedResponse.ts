/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { APIReference } from './APIReference';
import type { CredentialType } from './CredentialType';
/**
 * Redacted credential response (for read/list/patch).
 */
export type CredentialRedactedResponse = {
    /**
     * Whether the credential is enabled for injection.
     */
    active: boolean;
    /**
     * The (vendor, name, version) API this credential targets.
     */
    api: APIReference;
    /**
     * Creation timestamp (UTC).
     */
    created_at: string;
    /**
     * Identity that created the credential (its owner).
     */
    created_by?: (string | null);
    /**
     * Stable credential identifier, prefixed `cred_`.
     */
    credential_id: string;
    /**
     * Redacted, type-specific projection (hints/last-N chars; never the secret).
     */
    details?: (Record<string, any> | null);
    /**
     * Human-readable label.
     */
    name: string;
    /**
     * Credential provider; 'static' for stored secrets.
     */
    provider: string;
    /**
     * Opaque reference to the provider account, when applicable.
     */
    provider_account_ref?: (string | null);
    /**
     * OpenAPI server-variable values for URL template substitution.
     */
    server_variables?: (Record<string, string> | null);
    /**
     * Credential auth type (api_key, bearer_token, basic, oauth2).
     */
    type: CredentialType;
    /**
     * Last update timestamp (UTC).
     */
    updated_at?: (string | null);
};

