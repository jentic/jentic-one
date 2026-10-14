/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HeldExecutionResponse } from './HeldExecutionResponse';
/**
 * A page of held executions.
 */
export type HeldExecutionListResponse = {
    data: Array<HeldExecutionResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

