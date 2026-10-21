/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One scope the credential's vendor offers, which an edit can add or remove.
 */
export type ScopeOptionSchema = {
    /**
     * What the scope lets a holder do.
     */
    classification: ScopeOptionSchema.classification;
    /**
     * What the scope grants, in words.
     */
    description: string;
    /**
     * The scope as the vendor names it.
     */
    name: string;
};
export namespace ScopeOptionSchema {
    /**
     * What the scope lets a holder do.
     */
    export enum classification {
        READ = 'read',
        WRITE = 'write',
        ADMIN = 'admin',
    }
}

