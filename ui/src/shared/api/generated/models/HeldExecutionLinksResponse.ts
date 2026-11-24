/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * HAL-style links for a held execution.
 */
export type HeldExecutionLinksResponse = {
    /**
     * The execution record, once the approved call has run.
     */
    execution?: (string | null);
    /**
     * Explain why the agent made the call, for the reviewer. Present while it is pending and not yet explained; only the agent that filed it may use it.
     */
    explain?: (string | null);
    /**
     * The call's result: the upstream response, or why it never ran. Present once the call has finished.
     */
    result?: (string | null);
    /**
     * This held execution.
     */
    self: string;
    /**
     * Withdraw the held call. Present while it is pending.
     */
    withdraw?: (string | null);
};

