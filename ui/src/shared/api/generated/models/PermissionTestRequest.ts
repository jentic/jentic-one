/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for :test — dry-run a request shape against the binding's rules.
 */
export type PermissionTestRequest = {
    /**
     * HTTP method of the hypothetical request (case-insensitive).
     */
    method: string;
    /**
     * Optional OpenAPI operation id resolved from the request URL.
     */
    operation_id?: (string | null);
    /**
     * Path of the hypothetical request relative to the API's server URL (the spec's path, e.g. `/widgets/42`) — the path the broker evaluates rules on.
     */
    path: string;
};

