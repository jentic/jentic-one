/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Rule set list entry.
 */
export type RuleSetSummaryResponse = {
    created_at: string;
    created_by?: (string | null);
    /**
     * True when an org admin created the set. A curated set can be attached by any caller allowed to write a binding's rules and edited only by an org admin; any other set is attachable and editable by its creator or an org admin.
     */
    curated: boolean;
    description?: (string | null);
    name: string;
    rule_count: number;
    rule_set_id: string;
};

