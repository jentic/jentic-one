/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Confirm an OAuth session: the scopes to request and the agent's rules.
 *
 * The default variant — a body without ``kind`` is this one.
 */
export type OAuthConfirmSessionRequest = {
    /**
     * Agent to bind when the session has none; ignored if the session already has one.
     */
    agent_id?: (string | null);
    /**
     * Upstream scopes the approver confirmed (at most 100).
     */
    confirmed_scopes: Array<string>;
    /**
     * Review digest echoed from the review data; a mismatch is 409 review_stale. Required for an API target.
     */
    digest?: (string | null);
    /**
     * Agent ID from the review the approver saw; a mismatch is 409 review_stale. Required for an API target.
     */
    expected_agent_id?: (string | null);
    /**
     * Confirm variant; `oauth` (the default when omitted).
     */
    kind?: string;
    /**
     * Permission rules to bind for the agent (at most 100).
     */
    permission_rules?: Array<PermissionRuleSchema>;
};

