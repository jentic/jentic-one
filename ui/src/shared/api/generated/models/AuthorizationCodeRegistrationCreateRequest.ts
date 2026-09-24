/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create request for an authorization-code registration.
 */
export type AuthorizationCodeRegistrationCreateRequest = {
    api_vendor: string;
    authorize_url: string;
    /**
     * Catalog API slug this OAuth app targets (e.g. 'github.com/api.github.com'). The credential minted through this registration carries the same slug so its operations preview resolves against a real registered API.
     */
    catalog_api_id: string;
    client_id: string;
    client_secret: string;
    default_scopes?: (Array<string> | null);
    /**
     * Vendor family label ('GitHub'), shown on picker cards.
     */
    display_name: string;
    flow_kind: string;
    name: string;
    token_url: string;
};

