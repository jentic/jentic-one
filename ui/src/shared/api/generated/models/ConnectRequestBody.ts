/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for initiating a credential connect flow.
 */
export type ConnectRequestBody = {
    /**
     * Additional provider-specific connect parameters.
     */
    extra?: Record<string, string>;
    /**
     * OAuth scopes to request.
     */
    scopes?: Array<string>;
};

