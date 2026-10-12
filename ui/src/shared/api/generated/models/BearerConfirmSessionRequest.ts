/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Confirm a ``manual_bearer`` session with the bearer token to store.
 */
export type BearerConfirmSessionRequest = {
    agent_id?: (string | null);
    digest: string;
    expected_agent_id?: (string | null);
    kind: string;
    permission_rules: Array<PermissionRuleSchema>;
    token: string;
};

