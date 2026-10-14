/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Bind the approver's OAuth credential and re-consent it with the requested scopes.
 *
 * Refused when another agent is bound to the credential.
 */
export type ReauthorizeConfirmSessionRequest = {
    /**
     * Agent to bind when the session has none; ignored if the session already has one.
     */
    agent_id?: (string | null);
    /**
     * ID of the approver's OAuth credential to bind and re-consent.
     */
    credential_id: string;
    /**
     * Review digest echoed from the review data; a mismatch is 409 review_stale.
     */
    digest: string;
    /**
     * Agent ID from the review the approver saw; a mismatch is 409 review_stale.
     */
    expected_agent_id?: (string | null);
    /**
     * Confirm variant; always `reauthorize`.
     */
    kind: string;
    /**
     * Permission rules to bind for the agent (at most 100).
     */
    permission_rules: Array<PermissionRuleSchema>;
};

