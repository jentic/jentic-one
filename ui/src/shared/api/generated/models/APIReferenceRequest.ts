/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Relaxed variant for request bodies where partial identification is allowed.
 */
export type APIReferenceRequest = {
    /**
     * Catalog identity of the API (`domain[/sub-api]`, for example `nytimes.com/article_search`), when known; used for display only, not for matching.
     */
    catalog_api_id?: (string | null);
    /**
     * Name of the API within the vendor; empty to cover every API of the vendor.
     */
    name?: string;
    /**
     * Vendor of the API (for example `stripe.com`).
     */
    vendor: string;
    /**
     * Version of the API; empty to cover every version.
     */
    version?: string;
};

