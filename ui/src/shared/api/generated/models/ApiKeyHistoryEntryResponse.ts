/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single event in the API key audit trail.
 */
export type ApiKeyHistoryEntryResponse = {
    /**
     * Audited action, for example `rotate` or `revoke`.
     */
    action: string;
    /**
     * ID of the actor that performed the action.
     */
    actor_id?: (string | null);
    /**
     * Unique ID of the audit entry.
     */
    id: string;
    /**
     * When the action occurred (RFC 3339).
     */
    occurred_at: string;
    /**
     * Structured reason: `api_key_rotated` or `api_key_revoked`.
     */
    reason?: (string | null);
};

