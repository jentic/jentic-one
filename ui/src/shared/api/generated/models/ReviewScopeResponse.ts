/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One scope in the review's catalogue, flagged default and requested.
 */
export type ReviewScopeResponse = {
    /**
     * How much access the scope grants.
     */
    classification: ReviewScopeResponse.classification;
    /**
     * Whether the vendor requests the scope by default.
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
    /**
     * Whether the session's initiator asked for the scope.
     */
    requested: boolean;
};
export namespace ReviewScopeResponse {
    /**
     * How much access the scope grants.
     */
    export enum classification {
        READ = 'read',
        WRITE = 'write',
        ADMIN = 'admin',
    }
}

