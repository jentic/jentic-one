/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Confirm a ``manual_basic`` session with the username and password to store.
 */
export type BasicConfirmSessionRequest = {
    agent_id?: (string | null);
    digest: string;
    expected_agent_id?: (string | null);
    kind: string;
    password: string;
    permission_rules: Array<PermissionRuleSchema>;
    username: string;
};

