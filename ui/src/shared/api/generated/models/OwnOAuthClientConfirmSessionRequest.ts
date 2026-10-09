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
    agent_id?: (string | null);
    authorize_url: string;
    client_id: string;
    client_secret: string;
    confirmed_scopes?: Array<string>;
    digest: string;
    expected_agent_id?: (string | null);
    kind: string;
    permission_rules: Array<PermissionRuleSchema>;
    token_url: string;
};

