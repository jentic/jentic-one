/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HeldExecutionResponse } from './HeldExecutionResponse';
/**
 * A page of held executions.
 */
export type HeldExecutionListResponse = {
    /**
     * The page of held executions.
     */
    data: Array<HeldExecutionResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

