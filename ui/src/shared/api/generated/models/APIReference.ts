/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Identifies a target API — the strict (all-required) variant.
 *
 * Used in responses and as the canonical identity tuple.
 */
export type APIReference = {
    /**
     * Name of the API within the vendor.
     */
    name: string;
    /**
     * Vendor of the API (for example `stripe.com`).
     */
    vendor: string;
    /**
     * Version of the API.
     */
    version: string;
};

