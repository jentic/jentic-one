/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Resolve an ``awaiting_app`` session with the approver's own OAuth client.
 *
 * The client is stored on the session's credential (the same shape as a
 * ``direct_oauth2`` credential) and the session continues as an
 * authorization-code connect. Both endpoints are required: the API's
 * declared OAuth endpoints are never used for the approver's client, since
 * the spec may be agent-submitted and the token endpoint receives the
 * client secret.
 */
export type OwnOAuthClientConfirmSessionRequest = {
    /**
     * Agent to bind when the session has none; ignored if the session already has one.
     */
    agent_id?: (string | null);
    /**
     * Authorization endpoint URL of the OAuth app.
     */
    authorize_url: string;
    /**
     * Client ID of the approver's OAuth app.
     */
    client_id: string;
    /**
     * Client secret of the approver's OAuth app; write-only.
     */
    client_secret: string;
    /**
     * Upstream scopes to request (at most 100).
     */
    confirmed_scopes?: Array<string>;
    /**
     * Review digest echoed from the review data; a mismatch is 409 review_stale.
     */
    digest: string;
    /**
     * Agent ID from the review the approver saw; a mismatch is 409 review_stale.
     */
    expected_agent_id?: (string | null);
    /**
     * Confirm variant; always `own_oauth_client`.
     */
    kind: string;
    /**
     * Permission rules to bind for the agent (at most 100).
     */
    permission_rules: Array<PermissionRuleSchema>;
    /**
     * Token endpoint URL of the OAuth app.
     */
    token_url: string;
};

