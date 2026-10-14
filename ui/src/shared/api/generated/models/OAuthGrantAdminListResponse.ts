/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthGrantAdminResponse } from './OAuthGrantAdminResponse';
/**
 * A paginated list of OAuth grants (admin cross-view).
 */
export type OAuthGrantAdminListResponse = {
    /**
     * The page of grants.
     */
    data: Array<OAuthGrantAdminResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

