/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationResultResponse } from './OperationResultResponse';
/**
 * Cursor-paginated search results page.
 */
export type SearchResponse = {
    /**
     * The page of matching operations.
     */
    data: Array<OperationResultResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

