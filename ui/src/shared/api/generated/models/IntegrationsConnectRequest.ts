/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PermissionRuleSchema } from './PermissionRuleSchema';
export type IntegrationsConnectRequest = {
    agent_id?: (string | null);
    name?: (string | null);
    preferred_flow?: (string | null);
    reason?: (string | null);
    requested_permission_rules?: Array<PermissionRuleSchema>;
    requested_scopes?: Array<string>;
    /**
     * Vendor registry key (e.g. 'github')
     */
    vendor: string;
};

