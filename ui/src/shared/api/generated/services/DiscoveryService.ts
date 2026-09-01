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
     * OAuth authorization server metadata for the MCP resource
     * RFC 8414 metadata for the path-scoped issuer `{base}/mcp` (phase-3a §4.7).
     *
     * RFC 8414 §3.1 path insertion: this is the metadata URL clients derive for
     * the issuer `{base}/mcp` named by the protected-resource document. It
     * advertises the anonymous OAuth-client registration door (`/oauth-clients`)
     * and the public-client profile (`none` + PKCE S256) — the root document is a
     * separate, unchanged surface whose `registration_endpoint` remains the agent
     * `/register`.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static mcpOauthAuthorizationServer(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/oauth-authorization-server/mcp',
            errors: {
                400: `Bad Request`,
                404: `Interactive OAuth for MCP is disabled (\`server.mcp.oauth.enabled=false\`): the route answers the framework's plain route-not-found 404, indistinguishable from not-shipped.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * OAuth protected resource metadata (root alias)
     * Root-path alias of the MCP protected-resource document (phase-3a §4.7).
     *
     * Compatibility fallback for clients that probe the root
     * `/.well-known/oauth-protected-resource` instead of following the 401's
     * `resource_metadata` pointer (the documented Claude Code behaviour). Safe
     * because this deployment has exactly one OAuth-protected resource, so the
     * root and path-scoped documents are the same body.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static oauthProtectedResource(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/.well-known/oauth-protected-resource',
            errors: {
                400: `Bad Request`,
                404: `Interactive OAuth for MCP is disabled (\`server.mcp.oauth.enabled=false\`): the route answers the framework's plain route-not-found 404, indistinguishable from not-shipped.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * OAuth protected resource metadata for the MCP resource
     * RFC 9728 protected-resource metadata for `{base}/mcp` (phase-3a §4.7).
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
                404: `Interactive OAuth for MCP is disabled (\`server.mcp.oauth.enabled=false\`): the route answers the framework's plain route-not-found 404, indistinguishable from not-shipped.`,
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
