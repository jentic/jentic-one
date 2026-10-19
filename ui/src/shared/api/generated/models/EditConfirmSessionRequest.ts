/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Approve an edit session's change with the final rules the reviewer chose.
 */
export type EditConfirmSessionRequest = {
    /**
     * The final scope set (at most 100). Scopes beyond the credential's grant start a vendor consent; the rules and scopes land together when it completes. Omit to keep the grant as it is.
     */
    confirmed_scopes?: (Array<string> | null);
    /**
     * Confirm variant; always `edit`.
     */
    kind: string;
    /**
     * The final rule list to apply, in order (at most 100). Replaces the binding's own rules, or its shared rule set's rules while one is attached.
     */
    permission_rules: Array<PermissionRuleSchema>;
    /**
     * Switch the binding back on if it is suspended.
     */
    resume?: boolean;
    /**
     * The revision the reviewer saw (`edit.revision.id`); a newer one is 409 revision_changed.
     */
    revision_id: string;
};

