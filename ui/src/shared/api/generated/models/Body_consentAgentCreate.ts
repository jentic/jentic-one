/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_consentAgentCreate = {
    /**
     * Name for the new agent; surrounding whitespace is trimmed.
     */
    agent_name: string;
    /**
     * Single-use consent handle embedded in the consent page.
     */
    consent_token: string;
    /**
     * Signed single-use agent-creation token embedded in the consent page.
     */
    create_state: string;
};

