/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EventResponse } from './EventResponse';
/**
 * Paginated list of events.
 */
export type EventListResponse = {
    /**
     * The page of events.
     */
    data: Array<EventResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

