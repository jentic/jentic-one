/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Agent representation in API responses.
 */
export type AgentResponse = {
    /**
     * When the agent was approved (RFC 3339); null if not approved.
     */
    approved_at?: (string | null);
    /**
     * ID of the user who approved the agent; null if not approved.
     */
    approved_by?: (string | null);
    /**
     * When the agent was created (RFC 3339).
     */
    created_at: string;
    /**
     * Reason given when the agent was denied; null if not denied.
     */
    denial_reason?: (string | null);
    /**
     * ID of the actor that denied the agent; null if not denied.
     */
    denied_by?: (string | null);
    /**
     * Free-text description of the agent; null if not set.
     */
    description?: (string | null);
    /**
     * Whether the agent currently has an active API key; only populated by `GET /agents/{agent_id}`, and false in other responses.
     */
    has_api_key?: boolean;
    /**
     * Unique ID of the agent.
     */
    id: string;
    /**
     * Human-readable name of the agent.
     */
    name: string;
    /**
     * ID of the user who owns the agent; null for an unclaimed self-registration.
     */
    owner_id?: (string | null);
    /**
     * ID of the parent agent; null when the agent has no parent.
     */
    parent_agent_id?: (string | null);
    /**
     * ID of the actor that registered the agent, or `self` for a self-registration.
     */
    registered_by: string;
    /**
     * Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled` or `archived`.
     */
    status: string;
};

