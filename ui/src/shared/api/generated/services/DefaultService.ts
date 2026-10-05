/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecideRequest } from '../models/DecideRequest';
import type { ExecutionApprovalListResponse } from '../models/ExecutionApprovalListResponse';
import type { ExecutionApprovalResponse } from '../models/ExecutionApprovalResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DefaultService {
    /**
     * List Execution Approvals
     * List execution approvals with optional state/agent filters.
     * @returns ExecutionApprovalListResponse Successful Response
     * @throws ApiError
     */
    public static listExecutionApprovals({
        state,
        agentId,
        cursor,
        limit = 25,
    }: {
        state?: (string | null),
        agentId?: (string | null),
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<ExecutionApprovalListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/execution-approvals',
            query: {
                'state': state,
                'agent_id': agentId,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get Execution Approval
     * Get the detail of one execution approval.
     * @returns ExecutionApprovalResponse Successful Response
     * @throws ApiError
     */
    public static getExecutionApproval({
        approvalId,
    }: {
        approvalId: string,
    }): CancelablePromise<ExecutionApprovalResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/execution-approvals/{approval_id}',
            path: {
                'approval_id': approvalId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Decide Execution Approval
     * Approve or deny a pending execution approval.
     *
     * ``decision`` must be ``"approved"`` or ``"denied"``. An optional ``reason``
     * is stored on the approval row for audit purposes.
     *
     * Approving flips the held job to QUEUED so the worker picks it up on the
     * next tick. Denying marks the job FAILED.
     * @returns ExecutionApprovalResponse Successful Response
     * @throws ApiError
     */
    public static decideExecutionApproval({
        approvalId,
        requestBody,
    }: {
        approvalId: string,
        requestBody: DecideRequest,
    }): CancelablePromise<ExecutionApprovalResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/execution-approvals/{approval_id}/:decide',
            path: {
                'approval_id': approvalId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
