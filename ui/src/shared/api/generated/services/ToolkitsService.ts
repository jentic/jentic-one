/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ToolkitAgentListResponse } from '../models/ToolkitAgentListResponse';
import type { ToolkitCreateRequest } from '../models/ToolkitCreateRequest';
import type { ToolkitCreateResponse } from '../models/ToolkitCreateResponse';
import type { ToolkitListResponse } from '../models/ToolkitListResponse';
import type { ToolkitResponse } from '../models/ToolkitResponse';
import type { ToolkitUpdateRequest } from '../models/ToolkitUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ToolkitsService {
    /**
     * List toolkits
     * List toolkits with cursor-based pagination.
     * @returns ToolkitListResponse Successful Response
     * @throws ApiError
     */
    public static listToolkits({
        cursor,
        limit = 50,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<ToolkitListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/toolkits',
            query: {
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
     * Create toolkit
     * Create a toolkit and issue its first API key.
     *
     * The plaintext key (`jntc_live_…`) is returned **once** in `api_key` and is
     * never retrievable again. Optional `credential_ids` bind existing credentials
     * at creation time; each inline bind emits a ``no_permission_rules`` warning
     * because the broker denies by default until rules are added.
     * @returns ToolkitCreateResponse Successful Response
     * @throws ApiError
     */
    public static createToolkit({
        requestBody,
    }: {
        requestBody: ToolkitCreateRequest,
    }): CancelablePromise<ToolkitCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/toolkits',
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
     * Delete toolkit
     * Permanently delete a toolkit and cascade-remove its keys, bindings, and permission rules.
     * @returns void
     * @throws ApiError
     */
    public static deleteToolkit({
        toolkitId,
    }: {
        toolkitId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/toolkits/{toolkit_id}',
            path: {
                'toolkit_id': toolkitId,
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
     * Get toolkit
     * Get a single toolkit by its `tk_…` ID.
     * @returns ToolkitResponse Successful Response
     * @throws ApiError
     */
    public static getToolkit({
        toolkitId,
    }: {
        toolkitId: string,
    }): CancelablePromise<ToolkitResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/toolkits/{toolkit_id}',
            path: {
                'toolkit_id': toolkitId,
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
     * Update toolkit
     * Update a toolkit's name, description, or active flag.
     * @returns ToolkitResponse Successful Response
     * @throws ApiError
     */
    public static updateToolkit({
        toolkitId,
        requestBody,
    }: {
        toolkitId: string,
        requestBody: ToolkitUpdateRequest,
    }): CancelablePromise<ToolkitResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/toolkits/{toolkit_id}',
            path: {
                'toolkit_id': toolkitId,
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
     * List agents bound to toolkit
     * List agents bound to a toolkit with cursor-based pagination.
     * @returns ToolkitAgentListResponse Successful Response
     * @throws ApiError
     */
    public static listToolkitAgents({
        toolkitId,
        cursor,
        limit = 50,
    }: {
        toolkitId: string,
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<ToolkitAgentListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/toolkits/{toolkit_id}/agents',
            path: {
                'toolkit_id': toolkitId,
            },
            query: {
                'cursor': cursor,
                'limit': limit,
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
