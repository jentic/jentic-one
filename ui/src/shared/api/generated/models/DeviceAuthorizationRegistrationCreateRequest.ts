/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create request for a device-authorization registration.
 */
export type DeviceAuthorizationRegistrationCreateRequest = {
    /**
     * Vendor registry key the registration connects.
     */
    api_vendor: string;
    /**
     * Device authorization endpoint URL.
     */
    authorization_endpoint: string;
    /**
     * Catalog API slug this OAuth app targets (e.g. 'github.com/api.github.com').
     */
    catalog_api_id: string;
    /**
     * OAuth client ID of the app.
     */
    client_id: string;
    /**
     * Scopes requested by default.
     */
    default_scopes?: (Array<string> | null);
    /**
     * Vendor family label ('GitHub'), shown on picker cards.
     */
    display_name: string;
    /**
     * OAuth flow the registration uses; always `device_authorization`.
     */
    flow_kind: string;
    /**
     * Admin-chosen name of the registration.
     */
    name: string;
    /**
     * Token endpoint URL.
     */
    token_endpoint: string;
};

