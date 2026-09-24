/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthGrantResponse } from './OAuthGrantResponse';
/**
 * A paginated list of OAuth grants.
 */
export type OAuthGrantListResponse = {
    data: Array<OAuthGrantResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

