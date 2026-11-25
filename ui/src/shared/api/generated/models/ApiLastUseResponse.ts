/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * When an agent last called one API through a bound credential.
 */
export type ApiLastUseResponse = {
    /**
     * Name of the API called; null when the recorded calls carry none.
     */
    api_name?: (string | null);
    /**
     * Vendor of the API called.
     */
    api_vendor: string;
    /**
     * Recorded API version, when known.
     */
    api_version?: (string | null);
    /**
     * When the agent last called this API through the credential (RFC 3339).
     */
    last_used_at: string;
};

