/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Create a shared permission rule set (theme 5 phase 1, Q-04).
 */
export type RuleSetCreateRequest = {
    description?: (string | null);
    /**
     * Unique human-readable name.
     */
    name: string;
    /**
     * Initial ordered rule list (first-match-wins, default-deny).
     */
    rules?: Array<PermissionRuleSchema>;
};

