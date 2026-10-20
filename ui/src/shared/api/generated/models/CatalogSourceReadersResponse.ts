/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Who may read a ``restricted`` source (operator view only).
 */
export type CatalogSourceReadersResponse = {
    /**
     * These actors may read the source.
     */
    actor_ids: Array<string>;
    /**
     * Any one of these permissions grants read access.
     */
    permissions: Array<string>;
};

