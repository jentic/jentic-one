/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SessionRevisionResponse } from './SessionRevisionResponse';
/**
 * A session's revisions, oldest first, one page at a time.
 */
export type SessionRevisionListResponse = {
    data: Array<SessionRevisionResponse>;
    /**
     * Whether the session holds revisions beyond this page.
     */
    has_more: boolean;
    /**
     * The page size the request was served at.
     */
    limit: number;
    /**
     * Opaque cursor for the next page; null when this is the last one.
     */
    next_cursor?: (string | null);
};

