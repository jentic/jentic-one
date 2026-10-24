/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The agent the session binds and its owner.
 */
export type ReviewAgentResponse = {
    /**
     * ID of the agent.
     */
    agent_id: string;
    /**
     * Name of the agent.
     */
    name?: (string | null);
    /**
     * ID of the agent's owner.
     */
    owner_id?: (string | null);
    /**
     * The agent's lifecycle status.
     */
    status: string;
};

