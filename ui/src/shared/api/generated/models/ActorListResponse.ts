/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActorSummaryResponse } from './ActorSummaryResponse';
/**
 * Paginated list of actors.
 */
export type ActorListResponse = {
    /**
     * The page of actors.
     */
    data: Array<ActorSummaryResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

