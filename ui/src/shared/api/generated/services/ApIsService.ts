/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiImportRequest } from '../models/ApiImportRequest';
import type { ApiListResponse } from '../models/ApiListResponse';
import type { ApiResponse } from '../models/ApiResponse';
import type { ApiUpdateRequest } from '../models/ApiUpdateRequest';
import type { SecuritySchemeListResponse } from '../models/SecuritySchemeListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ApIsService {
    /**
     * List Apis
     * List locally registered APIs with optional vendor filter and pagination.
     *
     * This is the **imported** registry — APIs present in this deployment. The
     * public catalog of importable-but-not-yet-imported APIs is a separate surface
     * (``GET /catalog``); the two are not blended.
     * @returns ApiListResponse Successful Response
     * @throws ApiError
     */
    public static listApis({
        vendor,
        cursor,
        limit = 50,
    }: {
        /**
         * Only return APIs from this vendor.
         */
        vendor?: (string | null),
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of APIs to return (1-200).
         */
        limit?: number,
    }): CancelablePromise<ApiListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis',
            query: {
                'vendor': vendor,
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
     * Import Apis
     * Import OpenAPI/Arazzo content as new API revisions (async).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static importApis({
        requestBody,
    }: {
        /**
         * Wrapper for a batch of import sources.
         */
        requestBody: ApiImportRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis',
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
     * Delete Api
     * Delete an API and all its revisions.
     * @returns void
     * @throws ApiError
     */
    public static deleteApi({
        vendor,
        name,
        version,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/apis/{vendor}/{name}/{version}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `The API can't be deleted because another record still references it (type \`api_delete_blocked\`). Nothing was deleted.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get Api
     * Retrieve a single API by its (vendor, name, version) identity.
     * @returns ApiResponse Successful Response
     * @throws ApiError
     */
    public static getApi({
        vendor,
        name,
        version,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
    }): CancelablePromise<ApiResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
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
     * Update Api
     * Partially update an API's presentation fields.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static updateApi({
        vendor,
        name,
        version,
        requestBody,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
        /**
         * Partial update payload for an API's presentation fields.
         */
        requestBody: ApiUpdateRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/apis/{vendor}/{name}/{version}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
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
    /**
     * List an API's revisions
     * List every revision imported for an API, newest first.
     *
     * Each revision gives its `state` (`draft`, `published`, `imported` or
     * `archived`), whether it `is_current` (the one served), its source, spec
     * digest, operation count and lifecycle timestamps, plus links to the
     * actions valid in its state. Repeat `state` to filter by several states.
     * Results are cursor-paginated: pass `next_cursor` as `cursor` while
     * `has_more` is true. Fetch one revision with
     * `GET /apis/{vendor}/{name}/{version}/revisions/{revision_id}`.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listApiRevisions({
        vendor,
        name,
        version,
        state,
        cursor,
        limit = 50,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
        /**
         * Only return revisions in these states; repeat to match several.
         */
        state?: (Array<string> | null),
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of revisions to return (1-200).
         */
        limit?: number,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/revisions',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
            },
            query: {
                'state': state,
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
     * Delete an archived API revision
     * Permanently delete an archived revision.
     *
     * Only an `archived` revision can be deleted — archive it first; any other
     * state is refused with 409 (`revision_state_conflict`). Deletion cannot
     * be undone. Requires `apis:write`. Returns 204.
     * @returns void
     * @throws ApiError
     */
    public static deleteRevision({
        vendor,
        name,
        version,
        revisionId,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
        /**
         * ID of the API revision.
         */
        revisionId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/apis/{vendor}/{name}/{version}/revisions/{revision_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'revision_id': revisionId,
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
     * Get an API revision
     * Get one revision's metadata and lifecycle state.
     *
     * Returns the same fields as an entry in the revision list: `state`,
     * `is_current`, source, spec digest, operation count, timestamps, and links
     * to the actions valid in its state. This is metadata only — download the
     * spec itself from `.../revisions/{revision_id}/openapi`. 404 if the API or
     * revision does not exist.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getApiRevision({
        vendor,
        name,
        version,
        revisionId,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
        /**
         * ID of the API revision.
         */
        revisionId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/revisions/{revision_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'revision_id': revisionId,
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
     * Archive an API revision
     * Retire a `draft` or `imported` revision without deleting it.
     *
     * The revision moves to `archived` and is kept for reference; it can then
     * be deleted. If it was the API's current revision, the API is left with
     * no live revision until another is promoted. A revision in any other
     * state is refused with 409 (`revision_state_conflict`). Requires
     * `apis:write`. Returns the archived revision.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static archiveRevision({
        vendor,
        name,
        version,
        revisionId,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
        /**
         * ID of the API revision.
         */
        revisionId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{vendor}/{name}/{version}/revisions/{revision_id}:archive',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'revision_id': revisionId,
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
     * Promote Revision
     * Promote a draft revision to published, archiving the current one.
     *
     * If the draft declares different server hosts than the API's current (or last
     * live) revision, or serves a host over plaintext http that was https-only, and
     * the API has credentials bound to agents, the caller also needs
     * ``credentials:write``; otherwise the promote is refused with 403
     * ``host_change_requires_operator``.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static promoteRevision({
        vendor,
        name,
        version,
        revisionId,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
        /**
         * ID of the API revision.
         */
        revisionId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{vendor}/{name}/{version}/revisions/{revision_id}:promote',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'revision_id': revisionId,
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
     * List security schemes for an API
     * List security schemes (with OAuth2 flow URLs) for the API's current revision.
     * @returns SecuritySchemeListResponse Successful Response
     * @throws ApiError
     */
    public static listApiSecuritySchemes({
        vendor,
        name,
        version,
    }: {
        /**
         * Vendor of the API.
         */
        vendor: string,
        /**
         * Name of the API.
         */
        name: string,
        /**
         * Version of the API.
         */
        version: string,
    }): CancelablePromise<SecuritySchemeListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/security-schemes',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
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
