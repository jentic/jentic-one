/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleListResponse } from '../models/PermissionRuleListResponse';
import type { PermissionRuleSchema } from '../models/PermissionRuleSchema';
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
        /**
         * Opaque cursor from a previous page's next_cursor; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of rule sets to return (1-200, default 50).
         */
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
     * Create a named, reusable list of permission rules.
     *
     * Attach the set to any number of agent-credential bindings
     * (`PUT /credentials/{credential_id}/agents/{agent_id}/rule-set`); editing
     * its rules later updates every attached binding at once, so a policy
     * change such as "block this operation everywhere" is a single edit.
     * Supply a unique `name`, an optional `description`, and optional initial
     * ordered `rules`. A set created by an `org:admin` is *curated*: any
     * `credentials:write` holder may attach it, but only an `org:admin` may
     * edit it. Returns the set with its rules; 409 (`rule_set_name_conflict`)
     * if the name is taken.
     * @returns RuleSetResponse Successful Response
     * @throws ApiError
     */
    public static createPermissionRuleSet({
        requestBody,
    }: {
        /**
         * Create a shared permission rule set (theme 5 phase 1, Q-04).
         */
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
     * Permanently delete a rule set that no binding uses.
     *
     * Detach the set from every binding first: while any binding still
     * references it the delete fails with 409 (`rule_set_in_use`) and nothing
     * changes. Same edit permission as updating the set (403
     * `rule_set_access_denied`). Returns 204.
     * @returns void
     * @throws ApiError
     */
    public static deletePermissionRuleSet({
        ruleSetId,
    }: {
        /**
         * ID of the permission rule set.
         */
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
        /**
         * ID of the permission rule set.
         */
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
     * Rename or re-describe a permission rule set
     * Change a rule set's `name` and/or `description`.
     *
     * The rules themselves are replaced separately, with
     * `PUT /permission-rule-sets/{rule_set_id}/rules`. A curated set is editable
     * only by an `org:admin`; any other set by its creator or an `org:admin`
     * (403 `rule_set_access_denied` otherwise). Returns the updated set with
     * its rules and the number of bindings referencing it; 409
     * (`rule_set_name_conflict`) if the new name is taken.
     * @returns RuleSetResponse Successful Response
     * @throws ApiError
     */
    public static updatePermissionRuleSet({
        ruleSetId,
        requestBody,
    }: {
        /**
         * ID of the permission rule set.
         */
        ruleSetId: string,
        /**
         * Rename or re-describe a rule set.
         */
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
                409: `Another rule set in the same scope already uses the new name (\`rule_set_name_conflict\`). Nothing was renamed.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Replace all rules in a permission rule set
     * Replace a rule set's entire ordered rule list with the body.
     *
     * This is a full replacement, not a merge: user rules missing from the body
     * are removed, and an empty list clears them. Platform-managed system rules
     * (`_system: true`) are kept. The request is idempotent.
     * Every binding attached to the set enforces the new rules immediately.
     * Same edit permission as updating the set (403 `rule_set_access_denied`).
     * Returns the stored rules in order.
     * @returns PermissionRuleListResponse Successful Response
     * @throws ApiError
     */
    public static replacePermissionRuleSetRules({
        ruleSetId,
        requestBody,
    }: {
        /**
         * ID of the permission rule set.
         */
        ruleSetId: string,
        /**
         * Request payload for the `replacePermissionRuleSetRules` operation.
         */
        requestBody: Array<PermissionRuleSchema>,
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
