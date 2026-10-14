/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuleSetSummaryResponse } from './RuleSetSummaryResponse';
/**
 * Paginated list of rule sets.
 */
export type RuleSetListResponse = {
    /**
     * The page of rule sets.
     */
    data: Array<RuleSetSummaryResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

