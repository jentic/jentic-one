/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One recorded attempt in a delivery's history.
 */
export type WebhookDeliveryAttemptResponse = {
    attempt_id: string;
    attempt_number: number;
    created_at: (string | null);
    delivery_id: string;
    duration_ms: (number | null);
    error: (string | null);
    status_code: (number | null);
};

