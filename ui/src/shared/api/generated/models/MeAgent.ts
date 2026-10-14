/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialBindingEntry } from './CredentialBindingEntry';
/**
 * Identity response for an agent actor.
 */
export type MeAgent = {
    /**
     * ID of the user who approved the agent; null if not approved.
     */
    approved_by?: (string | null);
    /**
     * Credentials bound directly to the agent.
     */
    credential_bindings?: Array<CredentialBindingEntry>;
    /**
     * Unique ID of the agent.
     */
    id: string;
    /**
     * Human-readable name of the agent.
     */
    name: string;
    /**
     * ID of the parent agent; null when the agent has no parent.
     */
    parent_agent_id?: (string | null);
    /**
     * Permissions currently granted to the agent, read live rather than from the token.
     */
    permissions: Array<string>;
    /**
     * Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled` or `archived`.
     */
    status: string;
    /**
     * Permissions in the presented bearer token; if narrower than `permissions`, obtain a new token to pick up newer grants.
     */
    token_permissions: Array<string>;
    /**
     * Actor type discriminator; always `agent`.
     */
    type?: string;
};

