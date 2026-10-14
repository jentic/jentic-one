/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * POST /register request body.
 */
export type RegisterRequest = {
    /**
     * Name for the new agent.
     */
    client_name: string;
    /**
     * Accepted for RFC 7591 compatibility and ignored; the agent uses the JWT-bearer grant.
     */
    grant_types?: (Array<string> | null);
    /**
     * A JSON Web Key Set containing at least one Ed25519 public key (kty=OKP, crv=Ed25519). RSA and other key types are not accepted.
     */
    jwks: Record<string, any>;
    /**
     * Space-separated permissions requested for the agent (at most 100 tokens).
     */
    scope?: (string | null);
    /**
     * Accepted for RFC 7591 compatibility and ignored; the agent uses `private_key_jwt`.
     */
    token_endpoint_auth_method?: (string | null);
};

