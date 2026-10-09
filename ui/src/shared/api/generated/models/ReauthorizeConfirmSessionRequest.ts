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
    agent_id?: (string | null);
    credential_id: string;
    digest: string;
    expected_agent_id?: (string | null);
    kind: string;
    permission_rules: Array<PermissionRuleSchema>;
};

