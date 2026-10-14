/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuditResponse } from './AuditResponse';
/**
 * Paginated list of audit entries.
 */
export type AuditListResponse = {
    /**
     * The page of audit entries.
     */
    data: Array<AuditResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

