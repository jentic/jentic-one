/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EventLinks } from './EventLinks';
import type { EventSeverity } from './EventSeverity';
/**
 * Event representation in API responses.
 */
export type EventResponse = {
    _links: EventLinks;
    /**
     * ID of the actor that caused the event; null if none.
     */
    actor_id?: (string | null);
    /**
     * Kind of actor that caused the event, e.g. `user` or `agent`; null if none.
     */
    actor_type?: (string | null);
    /**
     * When the event was created (RFC 3339).
     */
    created_at: string;
    /**
     * Type-specific structured payload; empty object when there is none.
     */
    data?: Record<string, any>;
    /**
     * Longer human-readable detail; null if none.
     */
    detail?: (string | null);
    /**
     * Unique ID of the event (`evt_…`).
     */
    event_id: string;
    /**
     * Whether the event needs someone to act on it.
     */
    requires_action: boolean;
    severity: EventSeverity;
    /**
     * Short human-readable summary of the event.
     */
    summary: string;
    /**
     * Trace ID of the request that produced the event, if any.
     */
    trace_id?: (string | null);
    /**
     * Namespaced event type, e.g. `import.completed`. Opaque string: historical events may carry types that are no longer emitted.
     */
    type: string;
};

