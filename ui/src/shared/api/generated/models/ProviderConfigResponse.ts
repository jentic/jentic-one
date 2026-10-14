/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A stored provider config with secret fields redacted.
 */
export type ProviderConfigResponse = {
    /**
     * Stored config with secret fields redacted.
     */
    config: Record<string, any>;
    /**
     * When the provider config was created (RFC 3339).
     */
    created_at: string;
    /**
     * Provider name (e.g. 'pipedream').
     */
    name: string;
    /**
     * When the provider config was last updated (RFC 3339); null if never updated.
     */
    updated_at?: (string | null);
};

