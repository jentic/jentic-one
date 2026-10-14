/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * API key metadata — retrievable even after revocation.
 */
export type ApiKeyInfoResponse = {
    /**
     * When the API key was first created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the actor that created the API key.
     */
    created_by?: (string | null);
    /**
     * Unique ID of the API key record.
     */
    id: string;
    /**
     * When the API key was last rotated (RFC 3339); null if never rotated.
     */
    rotated_at?: (string | null);
    /**
     * Key status: `active`, or `revoked` once revoked.
     */
    status: string;
};

