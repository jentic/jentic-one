/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionApprovalResponse } from './ExecutionApprovalResponse';
/**
 * Paginated list of execution approvals.
 */
export type ExecutionApprovalListResponse = {
    data: Array<ExecutionApprovalResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

