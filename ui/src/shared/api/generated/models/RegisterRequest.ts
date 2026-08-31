/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * POST /register request body.
 */
export type RegisterRequest = {
    client_name: string;
    grant_types?: (Array<string> | null);
    /**
     * A JSON Web Key Set containing at least one Ed25519 public key (kty=OKP, crv=Ed25519). RSA and other key types are not accepted.
     */
    jwks: Record<string, any>;
    scope?: (string | null);
    token_endpoint_auth_method?: (string | null);
};

