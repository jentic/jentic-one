/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The held call exactly as it runs if approved (credentials are injected at run time).
 */
export type HeldRequestResponse = {
    /**
     * Request body, if any.
     */
    body?: (string | null);
    /**
     * True when `body` is cut short for display.
     */
    body_truncated?: boolean;
    /**
     * HTTP method.
     */
    method: string;
    /**
     * Upstream URL including the query string.
     */
    url: string;
};

