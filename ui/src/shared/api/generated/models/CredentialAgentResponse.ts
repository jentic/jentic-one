/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Agent directly bound to a credential.
 */
export type CredentialAgentResponse = {
    /**
     * ID of the bound agent.
     */
    agent_id: string;
    /**
     * Name of the bound agent.
     */
    agent_name: string;
    /**
     * When the agent was bound to the credential (RFC 3339).
     */
    bound_at: string;
    /**
     * Shared permission rule set this binding points at, if any. While attached, the set's ordered list is the binding's effective policy; null means the binding's inline rules apply.
     */
    rule_set_id?: (string | null);
    /**
     * Status of the bound agent.
     */
    status: string;
    /**
     * True when the binding is soft-suspended (reversible cut-off): the binding and its rules survive, but the agent cannot execute through this credential until resumed.
     */
    suspended: boolean;
    /**
     * Why the binding is suspended: `api_deleted` when the API the credential serves was deleted; null for an active binding or a manual suspension. Cleared on resume.
     */
    suspended_reason?: (string | null);
};

