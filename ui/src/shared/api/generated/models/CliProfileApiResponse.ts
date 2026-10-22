/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One API a CLI profile declares, and whether it is live in the registry.
 */
export type CliProfileApiResponse = {
    /**
     * The api_id a vendor entry names the API by.
     */
    api_id: string;
    /**
     * The `jentic connect <vendor>` key that creates this API's credential; null when it is connected another way.
     */
    connect?: (string | null);
    /**
     * Whether the registry serves the API now.
     */
    live: boolean;
    /**
     * Registry API name.
     */
    name: string;
    /**
     * What the API is to the CLI. It decides the rule kind its binding takes: path rules for `rest`/`uploads`, `graphql` rules for `graphql`, `git` rules for `git`.
     */
    role: CliProfileApiResponse.role;
    /**
     * Where the API comes from: `catalog:<api_id>` (public catalog), `shipped:specs/<file>` (a spec shipped with Jentic) or `builtin:<api_id>` (served by the Broker itself).
     */
    source: string;
    /**
     * Registry vendor of the API.
     */
    vendor: string;
    /**
     * API version; null for a catalog API that is not imported yet.
     */
    version?: (string | null);
};
export namespace CliProfileApiResponse {
    /**
     * What the API is to the CLI. It decides the rule kind its binding takes: path rules for `rest`/`uploads`, `graphql` rules for `graphql`, `git` rules for `git`.
     */
    export enum role {
        REST = 'rest',
        GRAPHQL = 'graphql',
        GIT = 'git',
        UPLOADS = 'uploads',
    }
}

