/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecideHeldExecutionRequest } from '../models/DecideHeldExecutionRequest';
import type { HeldExecutionDetailResponse } from '../models/HeldExecutionDetailResponse';
import type { HeldExecutionListResponse } from '../models/HeldExecutionListResponse';
import type { HeldExecutionResponse } from '../models/HeldExecutionResponse';
import type { HeldExecutionState } from '../models/HeldExecutionState';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class HeldExecutionsService {
    /**
     * List held executions
     * List the held executions the caller may see, newest first.
     *
     * ``org:admin`` sees every one; a user sees those filed by agents they own;
     * an agent sees its own. Filter by ``state`` (e.g. ``pending``) or ``agent_id``.
     * @returns HeldExecutionListResponse Successful Response
     * @throws ApiError
     */
    public static listHeldExecutions({
        state,
        agentId,
        cursor,
        limit = 25,
    }: {
        /**
         * Only return held executions in this decision state, e.g. `pending`.
         */
        state?: (HeldExecutionState | null),
        /**
         * Only return held executions filed by this agent.
         */
        agentId?: (string | null),
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of held executions to return (1-100, default 25).
         */
        limit?: number,
    }): CancelablePromise<HeldExecutionListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/executions/holds',
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
     * Get a held execution
     * One held execution: its decision, where the call stands, and the held request.
     *
     * ``execution_status`` follows the call from ``held`` to its outcome; once it
     * has finished, ``_links.result`` returns that outcome. A held execution
     * outside the caller's visibility answers ``404``.
     * @returns HeldExecutionDetailResponse Successful Response
     * @throws ApiError
     */
    public static getHeldExecution({
        heldExecutionId,
    }: {
        /**
         * ID of the held execution.
         */
        heldExecutionId: string,
    }): CancelablePromise<HeldExecutionDetailResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/executions/holds/{held_execution_id}',
            path: {
                'held_execution_id': heldExecutionId,
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
     * Get a held execution's result
     * The finished call's outcome: the upstream response, or why it never ran.
     *
     * A call that ran returns its execution document — ``execution_id``,
     * ``status``, ``http_status``, ``duration_ms`` and the upstream body as
     * ``body_b64`` — as JSON. A call that never ran (denied, expired, or refused
     * at run time) returns the ``application/problem+json`` body explaining why.
     * Before the call has finished this answers ``409``; past its retention
     * window, ``410``.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getHeldExecutionResult({
        heldExecutionId,
    }: {
        /**
         * ID of the held execution.
         */
        heldExecutionId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/executions/holds/{held_execution_id}/result',
            path: {
                'held_execution_id': heldExecutionId,
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
    /**
     * Approve or deny a held execution
     * Decide a pending held execution — the agent's owner or an ``org:admin`` only.
     *
     * ``approve`` releases the call, which is re-authorized and run once; ``deny``
     * fails it with a permission-denied result. The first decision wins: deciding
     * one that is no longer pending (or has expired) answers ``409``. An agent
     * caller is always refused (``403``), whatever its permissions.
     * @returns HeldExecutionResponse Successful Response
     * @throws ApiError
     */
    public static decideHeldExecution({
        heldExecutionId,
        requestBody,
    }: {
        /**
         * ID of the held execution.
         */
        heldExecutionId: string,
        /**
         * Approve or deny a pending held execution.
         */
        requestBody: DecideHeldExecutionRequest,
    }): CancelablePromise<HeldExecutionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/executions/holds/{held_execution_id}:decide',
            path: {
                'held_execution_id': heldExecutionId,
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
     * Abandon a held execution — the agent that filed it only.
     *
     * The call becomes ``withdrawn`` and never runs; no result is written. Any
     * other caller gets ``404``, as if it did not exist; one that is no longer
     * pending (decided, expired or already withdrawn) answers ``409``. Owners and
     * admins use ``:decide`` with ``deny`` instead.
     * @returns HeldExecutionResponse Successful Response
     * @throws ApiError
     */
    public static withdrawHeldExecution({
        heldExecutionId,
    }: {
        /**
         * ID of the held execution.
         */
        heldExecutionId: string,
    }): CancelablePromise<HeldExecutionResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/executions/holds/{held_execution_id}:withdraw',
            path: {
                'held_execution_id': heldExecutionId,
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
