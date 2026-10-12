/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Confirm a ``manual_api_key`` session with the API key to store.
 */
export type ApiKeyConfirmSessionRequest = {
    agent_id?: (string | null);
    digest: string;
    expected_agent_id?: (string | null);
    key: string;
    kind: string;
    permission_rules: Array<PermissionRuleSchema>;
};

