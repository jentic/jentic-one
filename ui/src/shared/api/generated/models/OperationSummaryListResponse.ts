/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationSummaryResponse } from './OperationSummaryResponse';
/**
 * Cursor-paginated list of operations.
 */
export type OperationSummaryListResponse = {
    /**
     * The page of operations.
     */
    data: Array<OperationSummaryResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

