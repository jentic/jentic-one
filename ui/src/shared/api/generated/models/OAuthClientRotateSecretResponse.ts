/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Returned on secret rotation — the new one-time plaintext secret.
 */
export type OAuthClientRotateSecretResponse = {
    /**
     * The new client secret. Store it securely; the previous secret is now invalid.
     */
    client_secret: string;
};

