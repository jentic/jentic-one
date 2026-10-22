/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A CLI invocation token's record (never the token itself).
 */
export type CliInvocationResponse = {
    /**
     * Agent-reported subcommand path; null if not given.
     */
    command_title?: (string | null);
    /**
     * Name of the credential the token's calls use; null when the Broker selects it.
     */
    credential_name?: (string | null);
    /**
     * When the token stops working unless renewed (RFC 3339).
     */
    expires_at: string;
    /**
     * When the token stops working regardless of renewals (RFC 3339).
     */
    hard_expires_at: string;
    /**
     * Unique ID of the invocation.
     */
    id: string;
    /**
     * When the token was revoked (RFC 3339); null while live.
     */
    revoked_at?: (string | null);
    /**
     * W3C trace ID chosen by the server; every call the token makes is recorded under it.
     */
    trace_id: string;
    /**
     * Vendor the token is for.
     */
    vendor: string;
};

