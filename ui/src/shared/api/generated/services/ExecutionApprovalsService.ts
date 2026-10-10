/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecideRequest } from '../models/DecideRequest';
import type { ExecutionApprovalDetailResponse } from '../models/ExecutionApprovalDetailResponse';
import type { ExecutionApprovalListResponse } from '../models/ExecutionApprovalListResponse';
import type { ExecutionApprovalResponse } from '../models/ExecutionApprovalResponse';
import type { ExecutionApprovalState } from '../models/ExecutionApprovalState';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ExecutionApprovalsService {
    /**
     * List execution approvals
     * List the approvals the caller may review, newest first.
     *
     * ``org:admin`` sees every approval; a user sees approvals for agents they
     * own; an agent sees its own. Filter by ``state`` (e.g. ``pending``) or
     * ``agent_id``.
     * @returns ExecutionApprovalListResponse Successful Response
     * @throws ApiError
     */
    public static listExecutionApprovals({
        state,
        agentId,
        cursor,
        limit = 25,
    }: {
        state?: (ExecutionApprovalState | null),
        agentId?: (string | null),
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<ExecutionApprovalListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/executions/approvals',
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
     * Get an execution approval
     * One approval with its agent, owner, matched rule and the held request body.
     *
     * An approval outside the caller's reviewer visibility answers ``404``.
     * @returns ExecutionApprovalDetailResponse Successful Response
     * @throws ApiError
     */
    public static getExecutionApproval({
        approvalId,
    }: {
        approvalId: string,
    }): CancelablePromise<ExecutionApprovalDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/executions/approvals/{approval_id}',
            path: {
                'approval_id': approvalId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Approve or deny an execution approval
     * Decide a pending approval — the agent's owner or an ``org:admin`` only.
     *
     * ``approve`` releases the held job to the worker, which re-authorizes and
     * runs it once; ``deny`` fails the job with a permission-denied result. The
     * first decision wins: deciding an approval that is no longer pending (or
     * has expired) answers ``409``. An agent caller is always refused (``403``),
     * whatever its scopes.
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
            url: '/executions/approvals/{approval_id}:decide',
            path: {
                'approval_id': approvalId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `Conflict`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Withdraw a held execution
     * Abandon a held execution — the identity that filed the hold only.
     *
     * The approval becomes ``withdrawn`` and its held job ``cancelled``; the call
     * never runs and no result is written. Any other caller gets ``404``, as if
     * the approval did not exist; an approval that is no longer pending (decided,
     * expired or already withdrawn) answers ``409``. It acts only on held
     * executions: owners and admins use ``:decide`` with ``deny`` instead.
     * @returns ExecutionApprovalResponse Successful Response
     * @throws ApiError
     */
    public static withdrawExecutionApproval({
        approvalId,
    }: {
        approvalId: string,
    }): CancelablePromise<ExecutionApprovalResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/executions/approvals/{approval_id}:withdraw',
            path: {
                'approval_id': approvalId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `Conflict`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
