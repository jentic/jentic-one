/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectSessionSummaryResponse } from './ConnectSessionSummaryResponse';
/**
 * Cursor-paginated envelope of connect-session summaries.
 */
export type ConnectSessionListResponse = {
    data: Array<ConnectSessionSummaryResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

