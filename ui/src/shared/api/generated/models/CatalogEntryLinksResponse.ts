/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Hypermedia links for a catalog entry.
 */
export type CatalogEntryLinksResponse = {
    /**
     * Human-facing GitHub tree URL for the entry, when known.
     */
    github?: (string | null);
    /**
     * URL of the catalog import action (`POST /catalog/{api_id}:import`).
     */
    import: string;
    /**
     * URL of the entry's vendor logo (`GET /catalog/{api_id}/logo`), served from the registry's cache. Present only when the manifest lists a logo; the request can still 404 if the upstream image turns out to be unavailable.
     */
    logo?: (string | null);
    /**
     * URL of the entry's operation preview.
     */
    operations: string;
    /**
     * Canonical URL of this catalog entry.
     */
    self: string;
};

