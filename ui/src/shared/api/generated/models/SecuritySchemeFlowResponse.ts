/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single OAuth2 flow within a security scheme.
 */
export type SecuritySchemeFlowResponse = {
    /**
     * Authorization endpoint URL; null if the flow has none.
     */
    authorization_url?: (string | null);
    /**
     * OAuth2 flow type (for example `authorizationCode` or `clientCredentials`).
     */
    flow_type: string;
    /**
     * Token refresh URL; null if the flow has none.
     */
    refresh_url?: (string | null);
    /**
     * Available scopes mapped to their descriptions.
     */
    scopes?: (Record<string, string> | null);
    /**
     * Token endpoint URL; null if the flow has none.
     */
    token_url?: (string | null);
};

