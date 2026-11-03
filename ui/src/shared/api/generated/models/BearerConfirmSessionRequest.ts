/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Confirm a ``manual_bearer`` session with the bearer token to store.
 */
export type BearerConfirmSessionRequest = {
    /**
     * Agent to bind when the session has none; ignored if the session already has one.
     */
    agent_id?: (string | null);
    /**
     * Review digest echoed from the review data; a mismatch is 409 review_stale.
     */
    digest: string;
    /**
     * Agent ID from the review the approver saw; a mismatch is 409 review_stale.
     */
    expected_agent_id?: (string | null);
    /**
     * Confirm variant; always `bearer`.
     */
    kind: string;
    /**
     * Permission rules to bind for the agent (at most 100).
     */
    permission_rules: Array<PermissionRuleSchema>;
    /**
     * The revision the approver reviewed (`revision.id`); required. Missing is 422 revision_required, a newer one 409 revision_changed.
     */
    revision_id?: (string | null);
    /**
     * Bearer token to store; write-only.
     */
    token: string;
};

