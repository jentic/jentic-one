/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One OAuth scope in a vendor's catalogue.
 */
export type VendorScopeResponse = {
    /**
     * How much access the scope grants.
     */
    classification: VendorScopeResponse.classification;
    /**
     * Whether the scope is requested unless the user deselects it.
     */
    default: boolean;
    /**
     * What the scope lets the credential do.
     */
    description: string;
    /**
     * The vendor's scope string.
     */
    name: string;
};
export namespace VendorScopeResponse {
    /**
     * How much access the scope grants.
     */
    export enum classification {
        READ = 'read',
        WRITE = 'write',
        ADMIN = 'admin',
    }
}

