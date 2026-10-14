/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Confirm a ``manual_basic`` session with the username and password to store.
 */
export type BasicConfirmSessionRequest = {
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
     * Confirm variant; always `basic`.
     */
    kind: string;
    /**
     * Password to store; write-only.
     */
    password: string;
    /**
     * Permission rules to bind for the agent (at most 100).
     */
    permission_rules: Array<PermissionRuleSchema>;
    /**
     * Username to store.
     */
    username: string;
};

