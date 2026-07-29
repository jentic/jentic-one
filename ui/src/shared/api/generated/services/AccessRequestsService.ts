/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccessRequestFileRequest } from '../models/AccessRequestFileRequest';
import type { AccessRequestListResponse } from '../models/AccessRequestListResponse';
import type { AccessRequestResponse } from '../models/AccessRequestResponse';
import type { AmendRequest } from '../models/AmendRequest';
import type { DecideRequest } from '../models/DecideRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AccessRequestsService {
    /**
     * List access requests
     * List access requests with cursor-based pagination.
     * @returns AccessRequestListResponse Successful Response
     * @throws ApiError
     */
    public static listAccessRequests({
        actorId,
        status,
        cursor,
        limit = 50,
    }: {
        actorId?: (string | null),
        status?: (string | null),
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<AccessRequestListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/access-requests',
            query: {
                'actor_id': actorId,
                'status': status,
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
     * File access request
     * File a new access request.
     * @returns AccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static fileAccessRequest({
        requestBody,
    }: {
        requestBody: AccessRequestFileRequest,
    }): CancelablePromise<AccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/access-requests',
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
    /**
     * Get access request
     * Get a single access request by ID.
     * @returns AccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static getAccessRequest({
        requestId,
    }: {
        requestId: string,
    }): CancelablePromise<AccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/access-requests/{request_id}',
            path: {
                'request_id': requestId,
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
     * Amend access request
     * Amend pending items on an access request.
     * @returns AccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static amendAccessRequest({
        requestId,
        requestBody,
    }: {
        requestId: string,
        requestBody: AmendRequest,
    }): CancelablePromise<AccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/access-requests/{request_id}:amend',
            path: {
                'request_id': requestId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * Decide access request items
     * Decide (approve/deny) items on an access request.
     * @returns AccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static decideAccessRequest({
        requestId,
        requestBody,
    }: {
        requestId: string,
        requestBody: DecideRequest,
    }): CancelablePromise<AccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/access-requests/{request_id}:decide',
            path: {
                'request_id': requestId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * Withdraw access request
     * Withdraw a pending access request.
     * @returns AccessRequestResponse Successful Response
     * @throws ApiError
     */
    public static withdrawAccessRequest({
        requestId,
    }: {
        requestId: string,
    }): CancelablePromise<AccessRequestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/access-requests/{request_id}:withdraw',
            path: {
                'request_id': requestId,
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
}
