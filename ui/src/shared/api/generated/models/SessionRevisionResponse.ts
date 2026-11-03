/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One version of what a connect session asks for.
 */
export type SessionRevisionResponse = {
    created_at: string;
    /**
     * Who began or amended the session; null for a rebase.
     */
    created_by?: (string | null);
    id: string;
    /**
     * 1 for the first ask, one more per amend.
     */
    number: number;
    /**
     * The ask as it was sent, stored verbatim. Its keys depend on which flow created the revision: an `:edit-connection` revision carries `rules`, `scopes`, `resume` and `reason`, where `rules` and `scopes` each hold either `set`, or `add` plus `remove`; a connect or `:amend` revision carries `requested_scopes`, `requested_permission_rules` and `reason`. Read `conflicts` here when present — a revision whose proposal reports conflicts is not a no-op even if its digest matches the current one.
     */
    proposal: Record<string, any>;
    /**
     * What confirming this revision would leave in place, as the comparison baseline for the next amend: the effective scopes and permission rules after the ask is applied. Compare it against the current revision's `result` to see what a confirm would actually change.
     */
    result: Record<string, any>;
};

