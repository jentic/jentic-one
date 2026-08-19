/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One delivery attempt record — the operator's debugging view.
 */
export type WebhookDeliveryResponse = {
    attempt_count: number;
    created_at: (string | null);
    delivery_id: string;
    endpoint_id: string;
    event_id: string;
    last_attempt_at: (string | null);
    last_error: (string | null);
    last_status_code: (number | null);
    next_attempt_at: (string | null);
    status: string;
};

