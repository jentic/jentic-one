/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionResponse } from './ExecutionResponse';
/**
 * Paginated list of executions.
 */
export type ExecutionListResponse = {
    /**
     * The page of execution records.
     */
    data: Array<ExecutionResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

