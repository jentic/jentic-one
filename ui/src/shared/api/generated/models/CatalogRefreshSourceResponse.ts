/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One refreshed source's run outcome.
 */
export type CatalogRefreshSourceResponse = {
    /**
     * Why a failed or skipped run did not apply, e.g. listing_timeout.
     */
    error_code?: (string | null);
    /**
     * The source's slug.
     */
    slug: string;
    /**
     * Run outcome: applied, suspect_empty (held by the mass-removal guard), superseded, busy (another run holds the source; this refresh applied nothing for it), disabled, failed, or skipped (the refresh deadline passed before it started; the scanner syncs it).
     */
    status: CatalogRefreshSourceResponse.status;
};
export namespace CatalogRefreshSourceResponse {
    /**
     * Run outcome: applied, suspect_empty (held by the mass-removal guard), superseded, busy (another run holds the source; this refresh applied nothing for it), disabled, failed, or skipped (the refresh deadline passed before it started; the scanner syncs it).
     */
    export enum status {
        APPLIED = 'applied',
        SUSPECT_EMPTY = 'suspect_empty',
        SUPERSEDED = 'superseded',
        BUSY = 'busy',
        DISABLED = 'disabled',
        FAILED = 'failed',
        SKIPPED = 'skipped',
    }
}

