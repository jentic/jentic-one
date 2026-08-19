/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for rotating an endpoint secret.
 */
export type WebhookSecretRotateRequest = {
    /**
     * How long the previous secret keeps working, so the far side can be updated without dropping in-flight events. 0 revokes immediately — correct for a leaked secret, at the cost of those events. Omit for the 24 hour default.
     */
    grace_seconds?: (number | null);
};

