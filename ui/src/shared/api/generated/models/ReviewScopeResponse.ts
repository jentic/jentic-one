/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ReviewScopeResponse = {
    classification: ReviewScopeResponse.classification;
    default: boolean;
    description: string;
    name: string;
    requested: boolean;
};
export namespace ReviewScopeResponse {
    export enum classification {
        READ = 'read',
        WRITE = 'write',
        ADMIN = 'admin',
    }
}

