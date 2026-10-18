/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Optional body for ``POST /catalog/sources/{slug}:notify``.
 */
export type CatalogSourceNotifyRequest = {
    /**
     * Ids the upstream says changed (at most 100). A hint only: the sync still lists the whole source.
     */
    external_ids?: Array<string>;
};

