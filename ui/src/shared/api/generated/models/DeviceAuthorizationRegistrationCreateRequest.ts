/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create request for a device-authorization registration.
 */
export type DeviceAuthorizationRegistrationCreateRequest = {
    api_vendor: string;
    authorization_endpoint: string;
    client_id: string;
    default_scopes?: (Array<string> | null);
    flow_kind: string;
    name: string;
    token_endpoint: string;
};

