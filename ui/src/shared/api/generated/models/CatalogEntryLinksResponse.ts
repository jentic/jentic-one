/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Hypermedia links for a catalog entry.
 */
export type CatalogEntryLinksResponse = {
    /**
     * URLs of the visible entries with the same `catalog_api_id` collapsed into this one (empty when nothing was collapsed).
     */
    alternates?: Array<string>;
    /**
     * Human-facing page for the entry at its source, when known.
     */
    human?: (string | null);
    /**
     * URL of the catalog import action (`POST /catalog/{entry_id}:import`).
     */
    import: string;
    /**
     * URL of the entry's operation preview.
     */
    operations: string;
    /**
     * Canonical URL of this catalog entry.
     */
    self: string;
};

