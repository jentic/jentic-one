/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * OAuth 2.0 error response (RFC 6749 §5.2 dialect).
 *
 * Spec-facing OAuth endpoints (token, RFC 7009 revocation, RFC 7591
 * registration) answer errors in this dialect rather than RFC 9457 Problem
 * Details, because real OAuth/MCP clients parse it.
 */
export type OAuthErrorResponse = {
    /**
     * ASCII error code, e.g. `invalid_request`, `invalid_grant`, `invalid_client`, `unsupported_grant_type`, `invalid_client_metadata`, `slow_down`.
     */
    error: string;
    /**
     * Human-readable explanation of the error.
     */
    error_description?: (string | null);
};

