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
    /**
     * When the rule set was created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the actor that created the rule set.
     */
    created_by?: (string | null);
    /**
     * True when an org admin created the set. A curated set can be attached by any caller allowed to write a binding's rules and edited only by an org admin; any other set is attachable and editable by its creator or an org admin.
     */
    curated: boolean;
    /**
     * Free-text description of the rule set; null if unset.
     */
    description?: (string | null);
    /**
     * Unique human-readable name of the rule set.
     */
    name: string;
    /**
     * Unique ID of the rule set (`prs_…`).
     */
    rule_set_id: string;
    /**
     * Ordered rule list (first-match-wins, default-deny).
     */
    rules: Array<PermissionRuleReadSchema>;
};

