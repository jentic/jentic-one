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
    /**
     * Catalog API slug this OAuth app targets (e.g. 'github.com/api.github.com').
     */
    catalog_api_id: string;
    client_id: string;
    default_scopes?: (Array<string> | null);
    /**
     * Vendor family label ('GitHub'), shown on picker cards.
     */
    display_name: string;
    flow_kind: string;
    name: string;
    token_endpoint: string;
};

