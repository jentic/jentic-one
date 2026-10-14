/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * POST /register 201 response.
 */
export type RegisterResponse = {
    /**
     * Single-use token a human presents to `POST /agents/{agent_id}:claim` to take ownership; omitted when the deployment does not issue claim tokens.
     */
    claim_token?: (string | null);
    /**
     * ID of the new agent, used as its OAuth client_id.
     */
    client_id: string;
    /**
     * Grant types the agent may use; always the JWT-bearer grant.
     */
    grant_types?: Array<string>;
    /**
     * Bearer token for polling `registration_client_uri`; shown once and expires after a configured time.
     */
    registration_access_token: string;
    /**
     * URL to poll for the registration status.
     */
    registration_client_uri: string;
    /**
     * Lifecycle status of the new agent; `pending` until an operator approves it.
     */
    status: string;
    /**
     * How the agent authenticates at the token endpoint; always `private_key_jwt`.
     */
    token_endpoint_auth_method?: string;
};

