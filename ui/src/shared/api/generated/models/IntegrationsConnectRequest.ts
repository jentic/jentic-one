/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema } from './jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema';
export type IntegrationsConnectRequest = {
    agent_id?: (string | null);
    preferred_flow?: (string | null);
    reason?: (string | null);
    requested_permission_rules?: Array<jentic_one__control__web__schemas__permission_rules__PermissionRuleSchema>;
    requested_scopes?: Array<string>;
    /**
     * Vendor registry key (e.g. 'github')
     */
    vendor: string;
};

