/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthGrantResponse } from './OAuthGrantResponse';
/**
 * A paginated list of OAuth grants.
 */
export type OAuthGrantListResponse = {
    /**
     * The page of OAuth grants.
     */
    data: Array<OAuthGrantResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

