/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where an API target's live revision came from (registry data).
 */
export type ReviewProvenanceResponse = {
    /**
     * Catalog identity of the API, when it came from the catalog.
     */
    catalog_api_id?: (string | null);
    /**
     * `catalog` for a public-catalog import; anything else is a submitted spec.
     */
    origin?: (string | null);
    /**
     * ID of the API's live registry revision.
     */
    revision_id: string;
    /**
     * Where the spec was fetched from, when known.
     */
    source_url?: (string | null);
    /**
     * ID of the actor who submitted the spec, when known.
     */
    submitted_by?: (string | null);
};

