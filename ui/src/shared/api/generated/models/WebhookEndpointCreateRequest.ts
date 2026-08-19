/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for creating a webhook endpoint.
 */
export type WebhookEndpointCreateRequest = {
    /**
     * Event types to subscribe to. Empty means all relayable types.
     */
    event_types?: Array<string>;
    name: string;
    /**
     * Required for notification endpoints: the URL signed events are POSTed to.
     */
    target_url?: (string | null);
};

