/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditRulesRequest } from './EditRulesRequest';
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Change an open session's ask; fields sent replace those parts, the rest stay.
 */
export type AmendSessionRequest = {
    /**
     * Why, shown to the reviewer.
     */
    reason?: (string | null);
    /**
     * Create sessions: the rules to ask for, replacing the current ask.
     */
    requested_permission_rules?: (Array<PermissionRuleSchema> | null);
    /**
     * Create sessions: the scopes to ask for, replacing the current ask.
     */
    requested_scopes?: (Array<string> | null);
    /**
     * Edit sessions: resume a suspended binding.
     */
    resume?: (boolean | null);
    /**
     * Edit sessions: the rule change, relative to the baseline.
     */
    rules?: (EditRulesRequest | null);
};

