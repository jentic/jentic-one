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
    agent_id?: (string | null);
    confirmed_scopes: Array<string>;
    digest?: (string | null);
    expected_agent_id?: (string | null);
    kind?: string;
    permission_rules?: Array<PermissionRuleSchema>;
};

