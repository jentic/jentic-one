/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Anonymous OAuth-client dynamic registration (``POST /oauth-clients``).
 */
export type OauthClientDcrMethodResponse = {
    /**
     * Registration admission posture (server.mcp.oauth.auto_approve_clients): 'auto' activates registrations immediately; 'manual' parks them pending operator approval. Only meaningful when enabled.
     */
    approval: OauthClientDcrMethodResponse.approval;
    enabled: boolean;
};
export namespace OauthClientDcrMethodResponse {
    /**
     * Registration admission posture (server.mcp.oauth.auto_approve_clients): 'auto' activates registrations immediately; 'manual' parks them pending operator approval. Only meaningful when enabled.
     */
    export enum approval {
        AUTO = 'auto',
        MANUAL = 'manual',
    }
}

