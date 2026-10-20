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
        /**
         * Only return events of one of these types; repeat the parameter for several.
         */
        eventType?: (Array<string> | null),
        /**
         * Only return events with one of these severities; repeat the parameter for several.
         */
        severity?: (Array<EventSeverity> | null),
        /**
         * Only return events whose `requires_action` flag matches.
         */
        requiresAction?: (boolean | null),
        /**
         * Only return events created at or after this time (RFC 3339).
         */
        from?: (string | null),
        /**
         * Only return events created before this time (RFC 3339).
         */
        to?: (string | null),
        /**
         * Only return events with this trace ID.
         */
        traceId?: (string | null),
        /**
         * Only return events caused by this actor.
         */
        actorId?: (string | null),
        /**
         * Only return events caused by this kind of actor, e.g. `user` or `agent`.
         */
        actorType?: (string | null),
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of events to return (1-100, default 25).
         */
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
     *
     * The open stream re-verifies the caller at the first poll or page after each
     * `admin.events.stream_recheck_seconds` interval (default 5 s) elapses. Each
     * page is scoped by the caller's current permissions, so a narrower
     * permission set narrows the stream. Permissions are those the credential
     * carries: API keys and user `at_` access tokens resolve them live, while a
     * login session JWT and an agent `at_` access token carry the set they were
     * issued with. The stream keeps re-verifying the token it was opened with,
     * so for those a permission change applies once that token expires and the
     * client reconnects. The stream ends
     * with a `stream_closed` frame (`{"type": "stream_closed", "reason": ...}`)
     * once the credential stops verifying (`refused`: expired, revoked, or its
     * user or agent disabled or deleted), the caller no longer holds
     * `events:read` or `org:admin` or must rotate their password (`forbidden`),
     * or the check or the event store cannot be answered (`unavailable`).
     * A reconnect is authenticated afresh.
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
        /**
         * Stream events created after this time (RFC 3339); defaults to now, and a future time is treated as now.
         */
        since?: (string | null),
        /**
         * Only return events of one of these types; repeat the parameter for several.
         */
        eventType?: (Array<string> | null),
        /**
         * Only return events with one of these severities; repeat the parameter for several.
         */
        severity?: (Array<EventSeverity> | null),
        /**
         * Only return events whose `requires_action` flag matches.
         */
        requiresAction?: (boolean | null),
        /**
         * Only return events with this trace ID.
         */
        traceId?: (string | null),
        /**
         * Only return events caused by this actor.
         */
        actorId?: (string | null),
        /**
         * Only return events caused by this kind of actor, e.g. `user` or `agent`.
         */
        actorType?: (string | null),
        /**
         * ID of the last event received (`evt_` plus 24 hex characters), to resume a stream after it; ignored if the event is not visible to the caller.
         */
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
        /**
         * ID of the event.
         */
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
