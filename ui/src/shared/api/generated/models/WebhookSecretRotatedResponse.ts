/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Rotation result, including the new one-time secret.
 */
export type WebhookSecretRotatedResponse = {
    endpoint_id: string;
    previous_secret_expires_at: (string | null);
    /**
     * The new secret. Shown once and never again.
     */
    secret: string;
};

