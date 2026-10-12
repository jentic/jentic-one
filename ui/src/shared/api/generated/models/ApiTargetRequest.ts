/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A registry API identity to connect a credential for.
 */
export type ApiTargetRequest = {
    /**
     * API name
     */
    name: string;
    /**
     * API vendor (e.g. 'stripe-com')
     */
    vendor: string;
    /**
     * API version
     */
    version: string;
};

