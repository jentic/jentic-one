/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CatalogRefreshSourceResponse } from './CatalogRefreshSourceResponse';
/**
 * Outcome of a catalog refresh, over the sources the caller may read.
 */
export type CatalogRefreshResponse = {
    /**
     * Live entries of the refreshed sources after the sync.
     */
    count: number;
    /**
     * Each refreshed source's outcome, in sync order.
     */
    sources?: Array<CatalogRefreshSourceResponse>;
    /**
     * refreshed when no refreshed source failed or was skipped; partial otherwise (the failed ones keep serving their last applied entries).
     */
    status?: CatalogRefreshResponse.status;
};
export namespace CatalogRefreshResponse {
    /**
     * refreshed when no refreshed source failed or was skipped; partial otherwise (the failed ones keep serving their last applied entries).
     */
    export enum status {
        REFRESHED = 'refreshed',
        PARTIAL = 'partial',
    }
}

