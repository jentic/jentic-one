/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * GET /register/{agent_id} response.
 */
export type RegistrationStatusResponse = {
    /**
     * ID of the agent, used as its OAuth client_id.
     */
    client_id: string;
    /**
     * Grant types the agent may use; always the JWT-bearer grant.
     */
    grant_types?: Array<string>;
    /**
     * Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled` or `archived`.
     */
    status: string;
    /**
     * How the agent authenticates at the token endpoint; always `private_key_jwt`.
     */
    token_endpoint_auth_method?: string;
};

