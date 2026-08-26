/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WebhookDeliveryAttemptListResponse } from '../models/WebhookDeliveryAttemptListResponse';
import type { WebhookDeliveryListResponse } from '../models/WebhookDeliveryListResponse';
import type { WebhookEndpointCreatedResponse } from '../models/WebhookEndpointCreatedResponse';
import type { WebhookEndpointCreateRequest } from '../models/WebhookEndpointCreateRequest';
import type { WebhookEndpointListResponse } from '../models/WebhookEndpointListResponse';
import type { WebhookEndpointResponse } from '../models/WebhookEndpointResponse';
import type { WebhookEndpointStatsResponse } from '../models/WebhookEndpointStatsResponse';
import type { WebhookEndpointUpdateRequest } from '../models/WebhookEndpointUpdateRequest';
import type { WebhookEventCatalogResponse } from '../models/WebhookEventCatalogResponse';
import type { WebhookSecretRotatedResponse } from '../models/WebhookSecretRotatedResponse';
import type { WebhookSecretRotateRequest } from '../models/WebhookSecretRotateRequest';
import type { WebhookTestQueuedResponse } from '../models/WebhookTestQueuedResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WebhooksService {
    /**
     * List per-attempt history for a delivery
     * Every recorded attempt for one delivery (newest first): status code, categorised error, response time, and when. The parent delivery keeps only the last outcome; this is the full timeline.
     * @returns WebhookDeliveryAttemptListResponse Successful Response
     * @throws ApiError
     */
    public static listDeliveryAttempts({
        deliveryId,
    }: {
        deliveryId: string,
    }): CancelablePromise<WebhookDeliveryAttemptListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/webhooks/deliveries/{delivery_id}/attempts',
            path: {
                'delivery_id': deliveryId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Resend a delivery
     * Requeues a delivery for another attempt — including a dead-lettered one, which is the point: an operator can retry after fixing the receiver. Requeues rather than sending inline, so the dispatcher stays the only thing doing outbound network I/O.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static resendDelivery({
        deliveryId,
    }: {
        deliveryId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/webhooks/deliveries/{delivery_id}:resend',
            path: {
                'delivery_id': deliveryId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List webhook endpoints
     * List all configured endpoints. Never includes secret material.
     * @returns WebhookEndpointListResponse Successful Response
     * @throws ApiError
     */
    public static listEndpoints(): CancelablePromise<WebhookEndpointListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/webhooks/endpoints',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Create a webhook endpoint
     * Creates an outbound notification endpoint and returns the signing secret **once**. Requires the privileged `webhooks:write` scope, and the creation is audited.
     * @returns WebhookEndpointCreatedResponse Successful Response
     * @throws ApiError
     */
    public static createEndpoint({
        requestBody,
    }: {
        requestBody: WebhookEndpointCreateRequest,
    }): CancelablePromise<WebhookEndpointCreatedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/webhooks/endpoints',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Delete a webhook endpoint
     * Delete an endpoint and its delivery history (audited).
     * @returns void
     * @throws ApiError
     */
    public static deleteEndpoint({
        endpointId,
    }: {
        endpointId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/webhooks/endpoints/{endpoint_id}',
            path: {
                'endpoint_id': endpointId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get a webhook endpoint
     * Inspect one endpoint.
     * @returns WebhookEndpointResponse Successful Response
     * @throws ApiError
     */
    public static getEndpoint({
        endpointId,
    }: {
        endpointId: string,
    }): CancelablePromise<WebhookEndpointResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/webhooks/endpoints/{endpoint_id}',
            path: {
                'endpoint_id': endpointId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Update a webhook endpoint
     * Partially updates an endpoint's configuration — its name, target URL, event-type subscription, or active state. Only the fields supplied are changed. Requires the privileged `webhooks:write` scope, and the change is audited. **Never** touches or returns the signing secret; rotation is the separate flow for that. Changing `event_types` affects only future fan-out, not deliveries already queued.
     * @returns WebhookEndpointResponse Successful Response
     * @throws ApiError
     */
    public static updateEndpoint({
        endpointId,
        requestBody,
    }: {
        endpointId: string,
        requestBody: WebhookEndpointUpdateRequest,
    }): CancelablePromise<WebhookEndpointResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/webhooks/endpoints/{endpoint_id}',
            path: {
                'endpoint_id': endpointId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List delivery attempts for an endpoint
     * The delivery log: what was sent, what came back, how many attempts, and when the next one is due. Dead-lettered rows stay here for inspection rather than being deleted, so a failure can be diagnosed after the fact.
     * @returns WebhookDeliveryListResponse Successful Response
     * @throws ApiError
     */
    public static listDeliveries({
        endpointId,
    }: {
        endpointId: string,
    }): CancelablePromise<WebhookDeliveryListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/webhooks/endpoints/{endpoint_id}/deliveries',
            path: {
                'endpoint_id': endpointId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Aggregate delivery stats for an endpoint
     * Summarises the endpoint's delivery health — counts by status, last-24h volume and failures, the most recent attempt, the next scheduled attempt, and the average response time — for the Overview view. Derived entirely from the delivery log; adds no new storage.
     * @returns WebhookEndpointStatsResponse Successful Response
     * @throws ApiError
     */
    public static getEndpointStats({
        endpointId,
    }: {
        endpointId: string,
    }): CancelablePromise<WebhookEndpointStatsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/webhooks/endpoints/{endpoint_id}/stats',
            path: {
                'endpoint_id': endpointId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Rotate an endpoint's signing secret
     * Issues a new secret and returns it once. The previous secret keeps working for a grace period so both sides can be updated without dropping events; pass `grace_seconds: 0` to revoke it immediately (correct for a leak).
     * @returns WebhookSecretRotatedResponse Successful Response
     * @throws ApiError
     */
    public static rotateSecret({
        endpointId,
        requestBody,
    }: {
        endpointId: string,
        requestBody?: (WebhookSecretRotateRequest | null),
    }): CancelablePromise<WebhookSecretRotatedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/webhooks/endpoints/{endpoint_id}:rotate-secret',
            path: {
                'endpoint_id': endpointId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Send a test event
     * Queues a synthetic `webhook.test` event so wiring can be confirmed without waiting for something real to happen. The payload is explicitly marked as a test so a receiver can avoid acting on it.
     * @returns WebhookTestQueuedResponse Successful Response
     * @throws ApiError
     */
    public static sendTestEvent({
        endpointId,
    }: {
        endpointId: string,
    }): CancelablePromise<WebhookTestQueuedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/webhooks/endpoints/{endpoint_id}:test',
            path: {
                'endpoint_id': endpointId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List subscribable event types
     * The canonical set of event types an endpoint can subscribe to — ``EventType.ALL`` minus the never-relayed set, and excluding the synthetic ``webhook.test``. Served so the UI's event picker cannot drift from the backend.
     * @returns WebhookEventCatalogResponse Successful Response
     * @throws ApiError
     */
    public static getEventCatalog(): CancelablePromise<WebhookEventCatalogResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/webhooks/event-catalog',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
