/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentResponse } from './AgentResponse';
/**
 * List of agents.
 */
export type AgentListResponse = {
    /**
     * The page of agents.
     */
    data: Array<AgentResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

