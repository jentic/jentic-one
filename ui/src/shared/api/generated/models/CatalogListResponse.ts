/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CatalogEntryResponse } from './CatalogEntryResponse';
/**
 * List of catalog entries plus status fields for the Discover status row.
 */
export type CatalogListResponse = {
    /**
     * Total entries in the whole manifest (pre-filter, pre-page).
     */
    catalog_total: number;
    /**
     * The page of catalog entries.
     */
    data: Array<CatalogEntryResponse>;
    /**
     * Whether another page follows.
     */
    has_more?: boolean;
    /**
     * Age of the cached manifest in seconds, or null when the cache is empty.
     */
    manifest_age_seconds?: (number | null);
    /**
     * Opaque keyset cursor for the next page (null when done).
     */
    next_cursor?: (string | null);
    /**
     * Count of whole-manifest registered entries with an upstream update available.
     */
    outdated_count?: number;
    /**
     * Count of whole-manifest entries already imported locally.
     */
    registered_count: number;
};

