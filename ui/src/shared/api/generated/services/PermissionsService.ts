/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionListResponse } from '../models/PermissionListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PermissionsService {
    /**
     * List the permission catalogue
     * List every permission, flagged with whether the caller may grant it to an agent.
     *
     * Each entry gives the permission `name`, a `description`, the permissions
     * it `implies`, and `grantable_by_caller` — whether `POST /agents` and
     * `PUT /agents/{agent_id}/permissions` would accept it from this caller.
     * `org:admin` is listed only to callers who hold it. Use it to build an
     * agent permission picker. Any signed-in caller may call it.
     * @returns PermissionListResponse Successful Response
     * @throws ApiError
     */
    public static listPermissions(): CancelablePromise<PermissionListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/permissions',
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
