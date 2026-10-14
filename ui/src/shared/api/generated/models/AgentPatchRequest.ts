/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for partially updating an agent.
 */
export type AgentPatchRequest = {
    /**
     * New description for the agent.
     */
    description?: (string | null);
    /**
     * New name for the agent.
     */
    name?: (string | null);
    /**
     * ID of the user to transfer ownership to; `org:admin` only.
     */
    owner_id?: (string | null);
};

