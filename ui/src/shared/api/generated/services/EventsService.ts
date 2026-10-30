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
     * List events
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
     * Stream events over SSE
     * Stream events as Server-Sent Events.
     *
     * The open stream re-verifies the caller at the first poll or page after each
     * `admin.events.stream_recheck_seconds` interval (default 5 s) elapses. Each
     * page is scoped by the caller's current permissions, so a narrower
     * permission set narrows the stream. Permissions are those the credential
     * carries: API keys and `at_` access tokens resolve them live (an ephemeral
     * agent `at_` keeps the subset it was minted with). A login session JWT
     * carries the set it was issued with, but removing any of a user's effective
     * permissions revokes their login sessions, so a stream opened with one ends
     * (`refused`) at the next check. The stream keeps re-verifying the token it
     * was opened with, so a permission added to a user reaches a login-session
     * stream once the client reconnects with a newer login JWT. The stream ends
     * with a `stream_closed` frame (`{"type": "stream_closed", "reason": ...}`)
     * once the credential stops verifying (`refused`: expired, revoked, or its
     * user or agent disabled or deleted), the caller no longer holds
     * `events:read` or `org:admin` or must rotate their password (`forbidden`),
     * or the check or the event store cannot be answered (`unavailable`).
     * A reconnect is authenticated afresh.
     * @returns string An open `text/event-stream` connection in the HTML Server-Sent Events wire format — not a JSON document. The server holds the response open and writes frames until the caller disconnects or the stream closes. Each frame is an `event: <name>` line, an optional `id: <id>` line, one `data: <JSON object>` line, and a blank line. Three frame kinds arrive:
     *
     * - **An event frame.** Its `event` is the event's own `type` (e.g. `import.completed`, an open set — retired kinds persist in history), its `id` the event's `evt_…` id (send it back as `Last-Event-ID` to resume), and its `data` the same `EventResponse` object `GET /events/{event_id}` returns.
     * - **A `heartbeat` frame**, with no `id` and `data` of `{"type": "heartbeat", "sent_at": "<RFC 3339>"}`, which keeps the connection open while no event matches.
     * - **A final `stream_closed` frame**, with `data` of `{"type": "stream_closed", "reason": "refused" | "forbidden" | "unavailable"}`, after which the server closes the stream; reconnect to continue.
     *
     * The response also carries `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Because the `200` and its headers are sent before the first frame, a failure that happens later arrives as a `stream_closed` frame rather than an error status.
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
    }): CancelablePromise<string> {
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
     * Get an event
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
