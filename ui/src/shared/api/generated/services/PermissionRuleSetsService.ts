/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__toolkits__PermissionRuleSchema } from '../models/jentic_one__control__web__schemas__toolkits__PermissionRuleSchema';
import type { PermissionRuleListResponse } from '../models/PermissionRuleListResponse';
import type { RuleSetCreateRequest } from '../models/RuleSetCreateRequest';
import type { RuleSetListResponse } from '../models/RuleSetListResponse';
import type { RuleSetResponse } from '../models/RuleSetResponse';
import type { RuleSetUpdateRequest } from '../models/RuleSetUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PermissionRuleSetsService {
    /**
     * List permission rule sets
     * List shared rule sets with per-set rule counts (cursor-paginated).
     * @returns RuleSetListResponse Successful Response
     * @throws ApiError
     */
    public static listPermissionRuleSets({
        cursor,
        limit = 50,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<RuleSetListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/permission-rule-sets',
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
     * Create permission rule set
     * Create a named, shareable ordered rule list (theme 5 rule grouping).
     *
     * N agent-credential bindings can point at one set, so `permissions:test`
     * and "revoke this operation everywhere" stay single-place edits.
     * @returns RuleSetResponse Successful Response
     * @throws ApiError
     */
    public static createPermissionRuleSet({
        requestBody,
    }: {
        requestBody: RuleSetCreateRequest,
    }): CancelablePromise<RuleSetResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/permission-rule-sets',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                409: `Conflict`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Delete permission rule set
     * Delete a rule set nothing references (409 `rule_set_in_use` otherwise).
     * @returns void
     * @throws ApiError
     */
    public static deletePermissionRuleSet({
        ruleSetId,
    }: {
        ruleSetId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/permission-rule-sets/{rule_set_id}',
            path: {
                'rule_set_id': ruleSetId,
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
     * Get permission rule set
     * Get a rule set with its ordered rules and referencing-binding count.
     * @returns RuleSetResponse Successful Response
     * @throws ApiError
     */
    public static getPermissionRuleSet({
        ruleSetId,
    }: {
        ruleSetId: string,
    }): CancelablePromise<RuleSetResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/permission-rule-sets/{rule_set_id}',
            path: {
                'rule_set_id': ruleSetId,
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
     * Update permission rule set
     * Rename or re-describe a rule set (creator or org admin).
     * @returns RuleSetResponse Successful Response
     * @throws ApiError
     */
    public static updatePermissionRuleSet({
        ruleSetId,
        requestBody,
    }: {
        ruleSetId: string,
        requestBody: RuleSetUpdateRequest,
    }): CancelablePromise<RuleSetResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/permission-rule-sets/{rule_set_id}',
            path: {
                'rule_set_id': ruleSetId,
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
     * Replace rule set rules
     * Replace the set's full ordered rule list (idempotent PUT).
     *
     * Every binding pointing at the set picks the new list up at once — the
     * single-place edit rule grouping exists for.
     * @returns PermissionRuleListResponse Successful Response
     * @throws ApiError
     */
    public static replacePermissionRuleSetRules({
        ruleSetId,
        requestBody,
    }: {
        ruleSetId: string,
        requestBody: Array<jentic_one__control__web__schemas__toolkits__PermissionRuleSchema>,
    }): CancelablePromise<PermissionRuleListResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/permission-rule-sets/{rule_set_id}/rules',
            path: {
                'rule_set_id': ruleSetId,
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
