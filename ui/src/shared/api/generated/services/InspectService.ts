/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationInspectResult } from '../models/OperationInspectResult';
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
     * @returns OperationInspectResult The resolved operation, rendered in the format negotiated from `Accept`. `application/json` (the default, and what `**` or an absent `Accept` gets) returns the structured result below. `text/markdown` returns the same result as a human-readable Markdown page, and `application/openapi+yaml` as a single-operation OpenAPI document — both are plain text, with no JSON envelope. Any other requested media type answers `406`.
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
        /**
         * Level of detail to return; only `summary` is currently supported.
         */
        detail?: 'summary' | 'full',
    }): CancelablePromise<OperationInspectResult> {
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
