/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A webhook endpoint. Deliberately carries no secret material.
 */
export type WebhookEndpointResponse = {
    active: boolean;
    allowed_cidrs: Array<string>;
    created_at: (string | null);
    endpoint_id: string;
    event_types: Array<string>;
    name: string;
    previous_secret_expires_at?: (string | null);
    target_url: (string | null);
};

