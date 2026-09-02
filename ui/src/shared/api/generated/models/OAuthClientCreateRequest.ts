/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for creating an OAuth client.
 */
export type OAuthClientCreateRequest = {
    /**
     * If set, restricts which scopes this client may request. An empty list denies all non-OIDC scopes. Null means unrestricted (all scopes permitted).
     */
    allowed_scopes?: (Array<string> | null);
    /**
     * What a user's consent grants for this client. ``user`` keeps today's act-as-user semantics; ``agent`` marks the client for agent-bound consent (the MCP path).
     */
    consent_model?: OAuthClientCreateRequest.consent_model;
    /**
     * Optional description of the client.
     */
    description?: (string | null);
    /**
     * Human-readable name for the client.
     */
    name: string;
    /**
     * Allowed OAuth callback URLs. At least one required.
     */
    redirect_uris: Array<string>;
    /**
     * Whether to show a consent screen during authorization. Set to false for trusted first-party integrations.
     */
    require_consent?: boolean;
    /**
     * Client authentication method at the token endpoint. ``client_secret_basic`` creates a confidential client with a generated secret; ``none`` creates a public (secret-less) client that relies on PKCE alone — no secret is generated or returned.
     */
    token_endpoint_auth_method?: OAuthClientCreateRequest.token_endpoint_auth_method;
};
export namespace OAuthClientCreateRequest {
    /**
     * What a user's consent grants for this client. ``user`` keeps today's act-as-user semantics; ``agent`` marks the client for agent-bound consent (the MCP path).
     */
    export enum consent_model {
        USER = 'user',
        AGENT = 'agent',
    }
    /**
     * Client authentication method at the token endpoint. ``client_secret_basic`` creates a confidential client with a generated secret; ``none`` creates a public (secret-less) client that relies on PKCE alone — no secret is generated or returned.
     */
    export enum token_endpoint_auth_method {
        CLIENT_SECRET_BASIC = 'client_secret_basic',
        NONE = 'none',
    }
}

