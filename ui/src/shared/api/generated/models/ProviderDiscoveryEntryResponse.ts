/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialType } from './CredentialType';
/**
 * Discovery metadata for a single credential provider.
 */
export type ProviderDiscoveryEntryResponse = {
    /**
     * OAuth2 redirect URI for providers that require it. When no explicit redirect_uri is configured, this is derived from the deployment's public origin (server.public_base_url or the request origin), so it reflects the exact callback the connect flow will register with the IdP. Add this URL to your OAuth app's allowed redirect URIs.
     */
    callback_url?: (string | null);
    /**
     * Whether the provider is fully configured and operational.
     */
    configured: boolean;
    /**
     * Provider identifier (registry key).
     */
    id: string;
    /**
     * Human-readable provider name.
     */
    label: string;
    /**
     * Whether the provider handles vendor sign-in on behalf of the user.
     */
    managed: boolean;
    /**
     * Wire-level credential types this provider supports.
     */
    types: Array<CredentialType>;
};

