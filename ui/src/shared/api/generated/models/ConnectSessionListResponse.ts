/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectSessionSummaryResponse } from './ConnectSessionSummaryResponse';
/**
 * Cursor-paginated envelope of connect-session summaries.
 */
export type ConnectSessionListResponse = {
    /**
     * The page of connect sessions.
     */
    data: Array<ConnectSessionSummaryResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

