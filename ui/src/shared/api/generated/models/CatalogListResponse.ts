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
     * Entries visible to the caller after collapsing (pre-filter, pre-page).
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
     * Opaque keyset cursor for the next page (null when done).
     */
    next_cursor?: (string | null);
    /**
     * Seconds since the least recently synced visible source last synced, or null when none has synced.
     */
    oldest_source_sync_age_seconds?: (number | null);
    /**
     * Count of visible registered entries with an upstream update available (pre-filter).
     */
    outdated_count?: number;
    /**
     * Count of visible entries already imported locally (pre-filter).
     */
    registered_count: number;
};

