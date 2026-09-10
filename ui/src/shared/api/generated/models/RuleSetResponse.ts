/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleReadSchema } from './PermissionRuleReadSchema';
/**
 * Rule set detail — the ordered rules plus its referencing-binding count.
 */
export type RuleSetResponse = {
    /**
     * How many agent-credential bindings currently point at this set.
     */
    binding_count: number;
    created_at: string;
    created_by?: (string | null);
    description?: (string | null);
    name: string;
    rule_set_id: string;
    rules: Array<PermissionRuleReadSchema>;
};

