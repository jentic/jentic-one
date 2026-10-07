/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EventListResponse } from '../models/EventListResponse';
import type { EventResponse } from '../models/EventResponse';
import type { EventSeverity } from '../models/EventSeverity';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class EventsService {
    /**
     * List Events
     * List events with optional filters.
     * @returns EventListResponse Successful Response
     * @throws ApiError
     */
    public static listEvents({
        eventType,
        severity,
        requiresAction,
        from,
        to,
        traceId,
        actorId,
        actorType,
        cursor,
        limit = 25,
    }: {
        eventType?: (Array<string> | null),
        severity?: (Array<EventSeverity> | null),
        requiresAction?: (boolean | null),
        from?: (string | null),
        to?: (string | null),
        traceId?: (string | null),
        actorId?: (string | null),
        actorType?: (string | null),
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<EventListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/events',
            query: {
                'event_type': eventType,
                'severity': severity,
                'requires_action': requiresAction,
                'from': from,
                'to': to,
                'trace_id': traceId,
                'actor_id': actorId,
                'actor_type': actorType,
                'cursor': cursor,
                'limit': limit,
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
     * Stream Events
     * Stream events as Server-Sent Events.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static streamEvents({
        since,
        eventType,
        severity,
        requiresAction,
        traceId,
        actorId,
        actorType,
        lastEventId,
    }: {
        since?: (string | null),
        eventType?: (Array<string> | null),
        severity?: (Array<EventSeverity> | null),
        requiresAction?: (boolean | null),
        traceId?: (string | null),
        actorId?: (string | null),
        actorType?: (string | null),
        lastEventId?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/events/stream',
            headers: {
                'Last-Event-ID': lastEventId,
            },
            query: {
                'since': since,
                'event_type': eventType,
                'severity': severity,
                'requires_action': requiresAction,
                'trace_id': traceId,
                'actor_id': actorId,
                'actor_type': actorType,
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
     * Get Event
     * Get an event by ID.
     * @returns EventResponse Successful Response
     * @throws ApiError
     */
    public static getEvent({
        eventId,
    }: {
        eventId: string,
    }): CancelablePromise<EventResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/events/{event_id}',
            path: {
                'event_id': eventId,
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
}
