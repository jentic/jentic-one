/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type IntegrationsConnectRequest = {
    agent_id?: (string | null);
    preferred_flow?: (string | null);
    reason?: (string | null);
    requested_scopes?: Array<string>;
    /**
     * Vendor registry key (e.g. 'github')
     */
    vendor: string;
};

