/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type VendorScopeResponse = {
    /**
     * How much the scope grants, for showing risk on a connect screen.
     */
    classification: VendorScopeResponse.classification;
    /**
     * Whether the platform requests this scope unless the caller opts out.
     */
    default: boolean;
    /**
     * What the scope allows, for display next to its name.
     */
    description: string;
    /**
     * Scope name as the vendor spells it, to send in a connect request.
     */
    name: string;
};
export namespace VendorScopeResponse {
    /**
     * How much the scope grants, for showing risk on a connect screen.
     */
    export enum classification {
        READ = 'read',
        WRITE = 'write',
        ADMIN = 'admin',
    }
}

