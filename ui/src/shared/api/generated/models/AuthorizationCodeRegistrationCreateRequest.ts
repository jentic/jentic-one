/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create request for an authorization-code registration.
 */
export type AuthorizationCodeRegistrationCreateRequest = {
    /**
     * Vendor registry key the registration connects.
     */
    api_vendor: string;
    /**
     * Authorization endpoint URL.
     */
    authorize_url: string;
    /**
     * Catalog API slug this OAuth app targets (e.g. 'github.com/api.github.com'). The credential minted through this registration carries the same slug so its operations preview resolves against a real registered API.
     */
    catalog_api_id: string;
    /**
     * OAuth client ID of the app.
     */
    client_id: string;
    /**
     * OAuth client secret of the app; stored encrypted and never returned.
     */
    client_secret: string;
    /**
     * Scopes requested by default.
     */
    default_scopes?: (Array<string> | null);
    /**
     * Vendor family label ('GitHub'), shown on picker cards.
     */
    display_name: string;
    /**
     * OAuth flow the registration uses; always `authorization_code`.
     */
    flow_kind: string;
    /**
     * Admin-chosen name of the registration.
     */
    name: string;
    /**
     * Token endpoint URL.
     */
    token_url: string;
};

