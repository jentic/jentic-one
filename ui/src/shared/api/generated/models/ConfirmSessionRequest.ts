/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
export type ConfirmSessionRequest = {
    agent_id?: (string | null);
    confirmed_scopes: Array<string>;
    permission_rules?: Array<PermissionRuleSchema>;
};

