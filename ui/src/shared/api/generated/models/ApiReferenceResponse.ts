/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Core API identifier triple plus derived host.
 */
export type ApiReferenceResponse = {
    /**
     * Host the API is served from, derived from its spec; null if it declares none.
     */
    host: (string | null);
    /**
     * Registry name of the API.
     */
    name: string;
    /**
     * Registry vendor of the API.
     */
    vendor: string;
    /**
     * Registry version of the API.
     */
    version: string;
};

