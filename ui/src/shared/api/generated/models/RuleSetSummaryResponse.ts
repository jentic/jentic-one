/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Rule set list entry.
 */
export type RuleSetSummaryResponse = {
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
     * Number of rules in the set.
     */
    rule_count: number;
    /**
     * Unique ID of the rule set (`prs_…`).
     */
    rule_set_id: string;
};

