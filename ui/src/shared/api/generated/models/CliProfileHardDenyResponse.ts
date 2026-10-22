/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A hard-deny list that applies to one of the profile's APIs.
 */
export type CliProfileHardDenyResponse = {
    /**
     * What the list guards.
     */
    description: string;
    /**
     * What the list holds.
     */
    kind: CliProfileHardDenyResponse.kind;
    /**
     * The API the list applies to.
     */
    role: CliProfileHardDenyResponse.role;
};
export namespace CliProfileHardDenyResponse {
    /**
     * What the list holds.
     */
    export enum kind {
        GRAPHQL_MUTATION = 'graphql_mutation',
        GRAPHQL_HELD_MUTATION = 'graphql_held_mutation',
        GIT_REF = 'git_ref',
    }
    /**
     * The API the list applies to.
     */
    export enum role {
        REST = 'rest',
        GRAPHQL = 'graphql',
        GIT = 'git',
        UPLOADS = 'uploads',
    }
}

