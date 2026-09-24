/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for updating an agent's JWKS (public keys).
 *
 * The JWKS must contain at least one Ed25519 public key and must not
 * contain any private key material.
 */
export type JwksUpdateRequest = {
    /**
     * JWKS containing public keys
     */
    jwks: Record<string, any>;
};

