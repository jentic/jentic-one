/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DiscoveryService {
    /**
     * JSON Web Key Set
     * Return the JWKS document with the active public signing keys (ES256).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static jwks(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/jwks.json',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * OAuth authorization server metadata
     * Return RFC 8414 authorization-server metadata (endpoints, grant types, algorithms).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static oauthAuthorizationServer(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/oauth-authorization-server',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get OAuth authorization server metadata for MCP clients
     * Tell an MCP client how to register and obtain tokens for `{base}/mcp`.
     *
     * Returns RFC 8414 authorization server metadata for the issuer `{base}/mcp`
     * — the URL an MCP client derives from the protected-resource document
     * (RFC 8414 §3.1). It lists the authorization, token and revocation
     * endpoints, the anonymous client registration endpoint (`/oauth-clients`),
     * the MCP tool scopes, and the public client profile: no client secret
     * (`none`) with PKCE `S256`. It differs from the root
     * `/.well-known/oauth-authorization-server` document, whose
     * `registration_endpoint` is the agent `/register`. Returns 404 when
     * interactive OAuth for MCP is disabled (`server.mcp.oauth.enabled=false`).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static mcpOauthAuthorizationServer(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/oauth-authorization-server/mcp',
            errors: {
                400: `Bad Request`,
                404: `Interactive OAuth for MCP is disabled (\`server.mcp.oauth.enabled=false\`): the route answers the framework's plain route-not-found 404, so the gate state is unobservable.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get MCP protected resource metadata (root fallback)
     * Serve the MCP protected-resource document at the root well-known path.
     *
     * Returns the same RFC 9728 body as
     * `/.well-known/oauth-protected-resource/mcp`: the resource `{base}/mcp`,
     * its authorization server, and the MCP tool scopes. It exists for clients
     * (such as Claude Code) that probe this root path instead of following the
     * `resource_metadata` pointer in a 401. That is safe while this deployment
     * has exactly one OAuth-protected resource. Returns 404 when interactive
     * OAuth for MCP is disabled (`server.mcp.oauth.enabled=false`).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static mcpOauthProtectedResourceRootAlias(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/oauth-protected-resource',
            errors: {
                400: `Bad Request`,
                404: `Interactive OAuth for MCP is disabled (\`server.mcp.oauth.enabled=false\`): the route answers the framework's plain route-not-found 404, so the gate state is unobservable.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * OAuth protected resource metadata for the MCP resource
     * RFC 9728 protected-resource metadata for `{base}/mcp`.
     *
     * Names the /mcp-scoped authorization server and the MCP tool scopes. The
     * same body is also served at the root well-known path for clients that
     * ignore the 401's `resource_metadata` pointer.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static mcpOauthProtectedResource(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/oauth-protected-resource/mcp',
            errors: {
                400: `Bad Request`,
                404: `Interactive OAuth for MCP is disabled (\`server.mcp.oauth.enabled=false\`): the route answers the framework's plain route-not-found 404, so the gate state is unobservable.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * External IdP login descriptor
     * Public capability hint: whether IdP login is enabled, and which provider.
     *
     * The SPA reads this before login to decide whether to show a "Continue with
     * <provider>" button. Unauthenticated and secret-free — it advertises only the
     * enabled flag and the provider name (never client secrets or endpoints).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static authIdpDescriptor(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/auth/idp',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
