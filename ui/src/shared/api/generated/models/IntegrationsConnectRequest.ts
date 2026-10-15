/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiTargetRequest } from './ApiTargetRequest';
import type { PermissionRuleSchema } from './PermissionRuleSchema';
/**
 * Start connecting a credential for a vendor or a registry API.
 */
export type IntegrationsConnectRequest = {
    /**
     * ID of the agent to bind the credential to on confirm; it must be an agent the caller owns (any agent for `org:admin`). Agent callers must omit it, and when omitted by a user the credential connects unbound.
     */
    agent_id?: (string | null);
    /**
     * Registry API to connect a credential for. Exactly one of vendor or api.
     */
    api?: (ApiTargetRequest | null);
    /**
     * Declared scheme name or kind to use for an api target
     */
    auth_type?: (string | null);
    /**
     * Label for the resulting credential; defaults to the vendor's display name.
     */
    name?: (string | null);
    /**
     * ID of the admin OAuth app registration to connect through; required when several active registrations offer the flow.
     */
    oauth_app_registration_id?: (string | null);
    /**
     * Flow kind to use if the source offers it; defaults to the source's first flow.
     */
    preferred_flow?: (string | null);
    /**
     * Free-text reason for the request, shown to the approver.
     */
    reason?: (string | null);
    /**
     * Permission rules proposed for the agent, shown pre-filled on the review page; nothing is bound until confirm.
     */
    requested_permission_rules?: Array<PermissionRuleSchema>;
    /**
     * Upstream scopes to request (at most 100).
     */
    requested_scopes?: Array<string>;
    /**
     * Vendor registry key (e.g. 'github'). Exactly one of vendor or api.
     */
    vendor?: (string | null);
};

