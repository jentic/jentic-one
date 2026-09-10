/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where to reach the deployment's public endpoints.
 *
 * Endpoint fields are absolute URLs whenever a base URL is known
 * (``auth.canonical_base_url``, else the origin the request arrived on);
 * metadata paths degrade to root-relative paths only when neither is
 * resolvable. ``null`` means the corresponding surface or gate is not
 * available on the process answering this request.
 */
export type CapabilitiesUrlsResponse = {
    /**
     * Agent dynamic-registration endpoint (RFC 7591, see auth.methods.agent_dcr); null when the auth surface is not mounted on this process.
     */
    agent_registration: (string | null);
    /**
     * RFC 8414 authorization-server metadata document for the platform issuer (its registration_endpoint is the agent DCR door /register); null when the auth surface is not mounted on this process.
     */
    authorization_server_metadata: (string | null);
    /**
     * RFC 8414 metadata document for the /mcp logical issuer — the one whose registration_endpoint is the OAuth-client DCR door /oauth-clients (see auth.methods.oauth_client_dcr). Null when the auth surface is not mounted on this process or server.mcp.oauth is disabled.
     */
    authorization_server_metadata_mcp: (string | null);
    /**
     * OAuth authorization endpoint (interactive sign-in entry point for idp and local_login); null when the auth surface is not mounted on this process.
     */
    authorize: (string | null);
    /**
     * Advertised broker base URL for data-plane traffic — the value a client needs to route agent traffic through this deployment's broker. Published only when the operator sets server.advertised_broker_url (http/https; userinfo, query, and fragment are stripped); the deployment's internal control-plane→broker hop URL (server.mcp.broker_url) is topology-private and never published. Null when unset or invalid.
     */
    broker: (string | null);
    /**
     * OAuth-client dynamic-registration endpoint (see auth.methods.oauth_client_dcr); null when the auth surface is not mounted on this process.
     */
    oauth_client_registration: (string | null);
    /**
     * RFC 9728 protected-resource metadata document for the MCP surface; null when the auth surface is not mounted on this process or server.mcp.oauth is disabled.
     */
    protected_resource_metadata: (string | null);
    /**
     * OAuth token endpoint (authorization_code, refresh_token, and the service-account jwt-bearer grant); null when the auth surface is not mounted on this process.
     */
    token: (string | null);
};

