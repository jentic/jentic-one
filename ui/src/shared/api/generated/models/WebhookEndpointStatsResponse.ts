/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Aggregate delivery health for an endpoint's Overview.
 *
 * All derived from ``webhook_deliveries`` — counts by status, last-24h volume
 * and failures, the most recent attempt, the next scheduled attempt, and the
 * average response time.
 */
export type WebhookEndpointStatsResponse = {
    avg_duration_ms: (number | null);
    counts_by_status: Record<string, number>;
    last_attempt_at: (string | null);
    last_duration_ms: (number | null);
    last_status_code: (number | null);
    next_attempt_at: (string | null);
    recent_failed: number;
    recent_total: number;
    total: number;
};

