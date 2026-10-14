/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for creating an agent manually.
 */
export type AgentCreateRequest = {
    /**
     * Optional free-text description of the agent.
     */
    description?: (string | null);
    /**
     * Human-readable name of the agent.
     */
    name: string;
    /**
     * Permissions to grant the agent; each must be grantable by the caller. Omit to grant the default agent permission set.
     */
    permissions?: (Array<string> | null);
};

