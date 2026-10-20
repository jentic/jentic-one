/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An operator-facing problem with one source's mapping or rows.
 */
export type CatalogSourceWarningResponse = {
    /**
     * The catalog API the warning concerns.
     */
    catalog_api_id: string;
    /**
     * `mapping_unmatched`: a mapping row matches no curated id, so it is ignored; `ownership_conflict`: an entry claims a local API another source owns.
     */
    code: string;
    /**
     * The source's id for the entry.
     */
    external_id: string;
    /**
     * `vendor/name/version` of the local API, for an ownership conflict.
     */
    local_api?: (string | null);
};

