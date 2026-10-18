/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CatalogSourceReadersResponse } from './CatalogSourceReadersResponse';
import type { CatalogSourceWarningResponse } from './CatalogSourceWarningResponse';
/**
 * One configured catalog source and its sync state.
 *
 * The operator fields (``enabled`` to ``warnings``) are present only for an
 * ``org:admin`` caller.
 */
export type CatalogSourceResponse = {
    /**
     * Failed syncs since the last success.
     */
    consecutive_failures: number;
    /**
     * Whether the source is synced (operator only).
     */
    enabled?: (boolean | null);
    /**
     * Source kind.
     */
    kind: string;
    /**
     * When the last successful sync finished.
     */
    last_sync_at: (string | null);
    /**
     * Entries the source currently lists.
     */
    live_entries: number;
    /**
     * Rank when sources list the same API; lower wins (operator only).
     */
    precedence?: (number | null);
    /**
     * Readers of a restricted source (operator only).
     */
    readers?: (CatalogSourceReadersResponse | null);
    /**
     * Source slug, the `entry_id` prefix.
     */
    slug: string;
    /**
     * Outcome of the last sync.
     */
    status: string;
    /**
     * A sync is requested or wanted and has not started yet.
     */
    sync_pending: boolean;
    /**
     * Source trust level (operator only).
     */
    trust?: (string | null);
    /**
     * `public` or `restricted` (readable only by its readers).
     */
    visibility: string;
    /**
     * Mapping and ownership problems (operator only).
     */
    warnings?: (Array<CatalogSourceWarningResponse> | null);
};

