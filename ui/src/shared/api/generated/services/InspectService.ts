/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class InspectService {
    /**
     * Inspect operation
     * Inspect an operation — resolve to full structural detail.
     *
     * `api.vendor`/`api.name`/`api.version` in the result is the canonical API
     * reference: the identity to use in credential scopes, revision pins and
     * other API references. `api.display_name` (optional) is a human-readable
     * label only.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static inspectOperation({
        id,
        operationId,
        revisionId,
        detail = 'summary',
    }: {
        /**
         * METHOD URL identifier
         */
        id?: (string | null),
        /**
         * Operation ID
         */
        operationId?: (string | null),
        /**
         * Pin to specific revision
         */
        revisionId?: (string | null),
        detail?: 'summary' | 'full',
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/inspect',
            query: {
                'id': id,
                'operation_id': operationId,
                'revision_id': revisionId,
                'detail': detail,
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
}
