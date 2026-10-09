/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Bind a credential the approver already holds (OAuth only if its grant covers the ask).
 */
export type ExistingCredentialConfirmSessionRequest = {
    agent_id?: (string | null);
    credential_id: string;
    digest: string;
    expected_agent_id?: (string | null);
    kind: string;
    permission_rules: Array<PermissionRuleSchema>;
};

