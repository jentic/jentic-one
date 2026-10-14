/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CredentialRedactedResponse } from './CredentialRedactedResponse';
/**
 * Paginated list of credentials.
 */
export type CredentialListResponse = {
    /**
     * The page of credentials.
     */
    data: Array<CredentialRedactedResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

