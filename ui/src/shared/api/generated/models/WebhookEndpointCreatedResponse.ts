/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WebhookEndpointResponse } from './WebhookEndpointResponse';
/**
 * Creation result, including the one-time secret.
 *
 * This is the **only** response that carries the plaintext secret (alongside
 * rotation). It is not recoverable afterwards: it is stored encrypted so the
 * platform can sign and verify with it, but exposing it again would make every
 * read a secret-disclosure endpoint. Lost secret ⇒ rotate.
 */
export type WebhookEndpointCreatedResponse = {
    endpoint: WebhookEndpointResponse;
    /**
     * Shown once and never again. Store it now.
     */
    secret: string;
};

