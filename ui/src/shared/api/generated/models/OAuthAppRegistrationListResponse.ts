/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthAppRegistrationResponse } from './OAuthAppRegistrationResponse';
/**
 * Paginated list of OAuth app registrations.
 */
export type OAuthAppRegistrationListResponse = {
    /**
     * The page of OAuth app registrations.
     */
    data: Array<OAuthAppRegistrationResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

