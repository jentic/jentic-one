/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for creating an OAuth client.
 */
export type OAuthClientCreateRequest = {
    /**
     * If set, restricts which scopes this client may request. Null means all scopes are permitted.
     */
    allowed_scopes?: (Array<string> | null);
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
};

