/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiImportResponse } from '../models/ApiImportResponse';
import type { CatalogEntryResponse } from '../models/CatalogEntryResponse';
import type { CatalogListResponse } from '../models/CatalogListResponse';
import type { CatalogSnoozeRequest } from '../models/CatalogSnoozeRequest';
import type { CatalogSourceListResponse } from '../models/CatalogSourceListResponse';
import type { CatalogSourceNotifyRequest } from '../models/CatalogSourceNotifyRequest';
import type { CatalogSourceSyncResponse } from '../models/CatalogSourceSyncResponse';
import type { OperationPreviewListResponse } from '../models/OperationPreviewListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CatalogService {
    /**
     * List Catalog
     * List a keyset page of the catalog entries visible to the caller.
     *
     * The catalog holds thousands of entries, so this is cursor-paginated like
     * ``GET /apis``: follow ``next_cursor`` until ``has_more`` is false. Visible
     * entries sharing a ``catalog_api_id`` collapse into one (registered first,
     * then source precedence) unless ``include_alternates`` is set.
     * ``catalog_total``/``registered_count``/``outdated_count`` count the visible
     * collapsed list before filters and paging, so the status row stays stable.
     * @returns CatalogListResponse Successful Response
     * @throws ApiError
     */
    public static listCatalog({
        q,
        registeredOnly = false,
        unregisteredOnly = false,
        outdatedOnly = false,
        includeSnoozed = false,
        cursor,
        limit = 50,
        includeAlternates = false,
    }: {
        /**
         * Search text matched against each entry's `catalog_api_id`; results are ranked by match quality.
         */
        q?: (string | null),
        /**
         * Only return entries already imported into the registry.
         */
        registeredOnly?: boolean,
        /**
         * Only return entries not yet imported; cannot be combined with `registered_only` or `outdated_only`.
         */
        unregisteredOnly?: boolean,
        /**
         * Only return imported entries with an upstream update available.
         */
        outdatedOnly?: boolean,
        /**
         * Treat entries whose update notification is snoozed as outdated.
         */
        includeSnoozed?: boolean,
        /**
         * Opaque cursor from a previous page's `next_cursor`; keep the other filters unchanged while paging.
         */
        cursor?: (string | null),
        /**
         * Maximum number of entries to return (1-200).
         */
        limit?: number,
        /**
         * Return every visible entry instead of collapsing entries that share a `catalog_api_id` into one.
         */
        includeAlternates?: boolean,
    }): CancelablePromise<CatalogListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog',
            query: {
                'q': q,
                'registered_only': registeredOnly,
                'unregistered_only': unregisteredOnly,
                'outdated_only': outdatedOnly,
                'include_snoozed': includeSnoozed,
                'cursor': cursor,
                'limit': limit,
                'include_alternates': includeAlternates,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List Catalog Sources
     * List the catalog sources the caller can see, with their sync state.
     *
     * An ``org:admin`` caller sees every configured source, plus its readers,
     * trust, precedence and a ``warnings`` list (mapping rows that match no entry,
     * entries claiming an API another source owns).
     * @returns CatalogSourceListResponse Successful Response
     * @throws ApiError
     */
    public static listCatalogSources(): CancelablePromise<CatalogSourceListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/sources',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Notify Catalog Source
     * Tell the catalog a push-capable source changed; a sync follows in the background.
     *
     * Single-flight per source: a notification joins a pending sync, and one
     * within the source's ``min_notify_interval_seconds`` of the last run's start
     * queues a sync for when that interval ends. ``external_ids`` is a hint only;
     * the sync still lists the whole source.
     * @returns CatalogSourceSyncResponse Successful Response
     * @throws ApiError
     */
    public static notifyCatalogSource({
        slug,
        requestBody,
    }: {
        /**
         * Catalog source slug, as `GET /catalog/sources` lists it.
         */
        slug: string,
        /**
         * Request payload for the `notifyCatalogSource` operation.
         */
        requestBody?: (CatalogSourceNotifyRequest | null),
    }): CancelablePromise<CatalogSourceSyncResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/sources/{slug}:notify',
            path: {
                'slug': slug,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `The source does not exist, the caller cannot see it, or it takes no push notifications.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Sync Catalog Source
     * Request a sync of one catalog source; it runs in the background.
     *
     * Answers ``202`` at once: the sync starts through the source's lease, after
     * any run in progress. A request while one is pending joins it.
     * ``force=true`` (``org:admin``) skips the mass-removal guard for that run.
     * @returns CatalogSourceSyncResponse Successful Response
     * @throws ApiError
     */
    public static syncCatalogSource({
        slug,
        force = false,
    }: {
        /**
         * Catalog source slug, as `GET /catalog/sources` lists it.
         */
        slug: string,
        /**
         * Apply the sync even when it would remove more entries than the source's mass-removal guard allows (`org:admin`).
         */
        force?: boolean,
    }): CancelablePromise<CatalogSourceSyncResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/sources/{slug}:sync',
            path: {
                'slug': slug,
            },
            query: {
                'force': force,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `The source does not exist or the caller cannot see it.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get Catalog Entry
     * Retrieve a single visible catalog entry by ``entry_id``.
     *
     * A hidden or unknown id is a ``404``; when the id looks like a bare
     * ``catalog_api_id`` the body's ``did_you_mean`` names the visible entry for it.
     * @returns CatalogEntryResponse Successful Response
     * @throws ApiError
     */
    public static getCatalogEntry({
        entryId,
    }: {
        /**
         * Catalog entry identity, `{source_slug}/{external_id}`; send every `/` unencoded.
         */
        entryId: string,
    }): CancelablePromise<CatalogEntryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/{entry_id}',
            path: {
                'entry_id': entryId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Preview Catalog Operations
     * Preview the operations of a catalog entry's spec (capped, offset-paginated).
     *
     * ``tag`` and ``q`` filter the spec's operations server-side before windowing,
     * so the UI's search box covers every operation in the spec and pages the
     * filtered set via ``offset``/``limit`` ("Load more").
     * @returns OperationPreviewListResponse Successful Response
     * @throws ApiError
     */
    public static previewCatalogOperations({
        entryId,
        offset,
        limit = 200,
        tag,
        q,
    }: {
        /**
         * Catalog entry identity, `{source_slug}/{external_id}`; send every `/` unencoded.
         */
        entryId: string,
        /**
         * Number of matching operations to skip.
         */
        offset?: number,
        /**
         * Maximum number of operations to return (0-200).
         */
        limit?: number,
        /**
         * Only return operations with a tag containing this text (case-insensitive).
         */
        tag?: (string | null),
        /**
         * Only return operations whose method, path, summary or operation ID contains this text (case-insensitive).
         */
        q?: (string | null),
    }): CancelablePromise<OperationPreviewListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/{entry_id}/operations',
            path: {
                'entry_id': entryId,
            },
            query: {
                'offset': offset,
                'limit': limit,
                'tag': tag,
                'q': q,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                409: `\`catalog_entry_has_no_spec\`: the entry is card-only (lists no spec).`,
                410: `\`catalog_spec_gone\`: the entry's source no longer has a spec for it.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `\`catalog_source_unavailable\`: the entry's source is throttled or down; retry after the \`Retry-After\` header.`,
            },
        });
    }
    /**
     * Import Catalog Entry
     * Enqueue an async import of a visible catalog entry into the local registry.
     * @returns ApiImportResponse Successful Response
     * @throws ApiError
     */
    public static importCatalogEntry({
        entryId,
        switchSource = false,
    }: {
        /**
         * Catalog entry identity, `{source_slug}/{external_id}`; send every `/` unencoded.
         */
        entryId: string,
        /**
         * When another catalog entry's import already holds this entry's `catalog_api_id`, supersede that API with this entry's spec (its credentials then point at it). Needs `credentials:write`; without it the import takes a distinct name and leaves that API untouched.
         */
        switchSource?: boolean,
    }): CancelablePromise<ApiImportResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/{entry_id}:import',
            path: {
                'entry_id': entryId,
            },
            query: {
                'switch_source': switchSource,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                409: `\`catalog_entry_has_no_spec\`: the entry is card-only (lists no spec).`,
                410: `\`catalog_spec_gone\`: the entry's source no longer has a spec for it.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Snooze Catalog Entry
     * Snooze the outstanding update notification for a catalog entry.
     *
     * Operator action (``events:write``): quiet the "update available" badge for a
     * known-and-accepted upstream change without adopting it. Body is optional — omit or send
     * ``{"snoozed_until": null}`` to mute until a newer upstream digest lands (the primary
     * per-API affordance); send an ISO-8601 ``snoozed_until`` for a time-boxed snooze. A
     * genuinely newer upstream change re-lights the badge automatically.
     * @returns void
     * @throws ApiError
     */
    public static snoozeCatalogEntry({
        entryId,
        requestBody,
    }: {
        /**
         * Catalog entry identity, `{source_slug}/{external_id}`; send every `/` unencoded.
         */
        entryId: string,
        /**
         * Request payload for the `snoozeCatalogEntry` operation.
         */
        requestBody?: (CatalogSnoozeRequest | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/{entry_id}:snooze',
            path: {
                'entry_id': entryId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                409: `The catalog entry has no outstanding update to snooze.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Unsnooze Catalog Entry
     * Clear a snooze for a catalog entry. Operator action (``events:write``).
     * @returns void
     * @throws ApiError
     */
    public static unsnoozeCatalogEntry({
        entryId,
    }: {
        /**
         * Catalog entry identity, `{source_slug}/{external_id}`; send every `/` unencoded.
         */
        entryId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/{entry_id}:unsnooze',
            path: {
                'entry_id': entryId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
