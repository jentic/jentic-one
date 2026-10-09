/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiTargetRequest } from './ApiTargetRequest';
import type { PermissionRuleSchema } from './PermissionRuleSchema';
export type IntegrationsConnectRequest = {
    agent_id?: (string | null);
    /**
     * Registry API to connect a credential for. Exactly one of vendor or api.
     */
    api?: (ApiTargetRequest | null);
    /**
     * Declared scheme name or kind to use for an api target
     */
    auth_type?: (string | null);
    name?: (string | null);
    oauth_app_registration_id?: (string | null);
    preferred_flow?: (string | null);
    reason?: (string | null);
    requested_permission_rules?: Array<PermissionRuleSchema>;
    requested_scopes?: Array<string>;
    /**
     * Vendor registry key (e.g. 'github'). Exactly one of vendor or api.
     */
    vendor?: (string | null);
};

