/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A rule in an edit's current or resulting access.
 */
export type EditRuleView = {
    /**
     * In a result: who put the rule in the request (the agent, or a reviewer); null for a rule the binding already has.
     */
    added_by?: (string | null);
    comment?: (string | null);
    /**
     * `allow`, `deny` or `require-approval`.
     */
    effect: string;
    /**
     * The rule's id; null for a rule the edit adds.
     */
    id: (string | null);
    match_mode?: string;
    methods?: (Array<string> | null);
    operations?: (Array<string> | null);
    path?: (string | null);
};

