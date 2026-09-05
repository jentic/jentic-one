/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where the deployment's sibling surfaces live.
 */
export type CapabilitiesUrlsResponse = {
    /**
     * Path of the RFC 8414 authorization-server metadata document; null when the auth surface is not mounted on this deployment.
     */
    authorization_server_metadata: (string | null);
    /**
     * Advertised broker base URL for data-plane traffic — the value a client needs to route agent traffic through this deployment's broker (server.advertised_broker_url, falling back to server.mcp.broker_url, the URL the deployment already uses for its own control-plane→broker hop). Userinfo is stripped; null when neither is configured. Split deployments whose internal broker URL is not client-reachable should set server.advertised_broker_url explicitly.
     */
    broker: (string | null);
};

