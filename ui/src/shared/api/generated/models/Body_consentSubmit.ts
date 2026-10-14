/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_consentSubmit = {
    /**
     * `approve` or `deny`; any value other than `deny` is treated as approval.
     */
    action: string;
    /**
     * ID of the agent to connect the client to; sent only by the agent-picker consent page.
     */
    agent_id?: (string | null);
    /**
     * Single-use consent handle embedded in the consent page.
     */
    consent_token: string;
};

