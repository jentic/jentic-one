/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuleSetSummaryResponse } from './RuleSetSummaryResponse';
/**
 * Paginated list of rule sets.
 */
export type RuleSetListResponse = {
    data: Array<RuleSetSummaryResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

