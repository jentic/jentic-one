/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__toolkits__PermissionRuleSchema } from '../models/jentic_one__control__web__schemas__toolkits__PermissionRuleSchema';
import type { PermissionRuleListResponse } from '../models/PermissionRuleListResponse';
import type { PermissionsPatchRequest } from '../models/PermissionsPatchRequest';
import type { PermissionTestRequest } from '../models/PermissionTestRequest';
import type { PermissionTestResponse } from '../models/PermissionTestResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ToolkitPermissionsService {
    /**
     * List binding permission rules
     * List the fine-grained PBAC rules for a `(toolkit, credential)` binding.
     * @returns PermissionRuleListResponse Successful Response
     * @throws ApiError
     */
    public static listToolkitPermissions({
        toolkitId,
        credentialId,
    }: {
        toolkitId: string,
        credentialId: string,
    }): CancelablePromise<PermissionRuleListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/toolkits/{toolkit_id}/credentials/{credential_id}/permissions',
            path: {
                'toolkit_id': toolkitId,
                'credential_id': credentialId,
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
     * Patch binding permission rules
     * Additively add and/or remove permission rules on a binding.
     * @returns PermissionRuleListResponse Successful Response
     * @throws ApiError
     */
    public static patchPermissions({
        toolkitId,
        credentialId,
        requestBody,
    }: {
        toolkitId: string,
        credentialId: string,
        requestBody: PermissionsPatchRequest,
    }): CancelablePromise<PermissionRuleListResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/toolkits/{toolkit_id}/credentials/{credential_id}/permissions',
            path: {
                'toolkit_id': toolkitId,
                'credential_id': credentialId,
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
     * Replace binding permission rules
     * Replace the full set of permission rules for a binding (idempotent PUT).
     * @returns PermissionRuleListResponse Successful Response
     * @throws ApiError
     */
    public static replacePermissions({
        toolkitId,
        credentialId,
        requestBody,
    }: {
        toolkitId: string,
        credentialId: string,
        requestBody: Array<jentic_one__control__web__schemas__toolkits__PermissionRuleSchema>,
    }): CancelablePromise<PermissionRuleListResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/toolkits/{toolkit_id}/credentials/{credential_id}/permissions',
            path: {
                'toolkit_id': toolkitId,
                'credential_id': credentialId,
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
     * Dry-run permission evaluation
     * Answer "what would the broker do for this request?" without calling upstream.
     *
     * Evaluates the same **vendor-pooled** rule set the broker sees at request
     * time — rules from all same-vendor bindings on this toolkit compete in one
     * ordered list. The response names which binding contributed the matching
     * rule, which is not obvious from the toolkit id alone under pooling.
     * @returns PermissionTestResponse Successful Response
     * @throws ApiError
     */
    public static testToolkitPermissions({
        toolkitId,
        credentialId,
        requestBody,
    }: {
        toolkitId: string,
        credentialId: string,
        requestBody: PermissionTestRequest,
    }): CancelablePromise<PermissionTestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/toolkits/{toolkit_id}/credentials/{credential_id}/permissions:test',
            path: {
                'toolkit_id': toolkitId,
                'credential_id': credentialId,
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
