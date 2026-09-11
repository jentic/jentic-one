/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ToolkitKeyCreateRequest } from '../models/ToolkitKeyCreateRequest';
import type { ToolkitKeyCreateResponse } from '../models/ToolkitKeyCreateResponse';
import type { ToolkitKeyListResponse } from '../models/ToolkitKeyListResponse';
import type { ToolkitKeyResponse } from '../models/ToolkitKeyResponse';
import type { ToolkitKeyUpdateRequest } from '../models/ToolkitKeyUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ToolkitKeysService {
    /**
     * List toolkit keys
     * List a toolkit's API keys (redacted; only `key_preview` is shown).
     * @returns ToolkitKeyListResponse Successful Response
     * @throws ApiError
     */
    public static listKeys({
        toolkitId,
        cursor,
        limit = 50,
    }: {
        toolkitId: string,
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<ToolkitKeyListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/toolkits/{toolkit_id}/keys',
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
    /**
     * @deprecated
     * Issue toolkit key (retired)
     * Always `410 toolkit_keys_retired` — toolkit keys are retired (theme-5
     * Phase 4). Register a service account and use its `sak_` key instead.
     * Existing keys keep working (as their migrated service accounts) and can
     * still be listed, revoked, and deleted here.
     * @returns ToolkitKeyCreateResponse Successful Response
     * @throws ApiError
     */
    public static createKey({
        toolkitId,
        requestBody,
    }: {
        toolkitId: string,
        requestBody: ToolkitKeyCreateRequest,
    }): CancelablePromise<ToolkitKeyCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/toolkits/{toolkit_id}/keys',
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
     * Revoke toolkit key
     * Revoke (delete) a toolkit API key. Callers using it are rejected immediately.
     * @returns void
     * @throws ApiError
     */
    public static deleteKey({
        toolkitId,
        keyId,
    }: {
        toolkitId: string,
        keyId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/toolkits/{toolkit_id}/keys/{key_id}',
            path: {
                'toolkit_id': toolkitId,
                'key_id': keyId,
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
     * Update toolkit key
     * Update a key's label, IP allowlist, or revoked flag.
     * @returns ToolkitKeyResponse Successful Response
     * @throws ApiError
     */
    public static updateKey({
        toolkitId,
        keyId,
        requestBody,
    }: {
        toolkitId: string,
        keyId: string,
        requestBody: ToolkitKeyUpdateRequest,
    }): CancelablePromise<ToolkitKeyResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/toolkits/{toolkit_id}/keys/{key_id}',
            path: {
                'toolkit_id': toolkitId,
                'key_id': keyId,
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
}
