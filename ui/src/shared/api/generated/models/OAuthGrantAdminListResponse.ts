/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthGrantAdminResponse } from './OAuthGrantAdminResponse';
/**
 * A paginated list of OAuth grants (admin cross-view).
 */
export type OAuthGrantAdminListResponse = {
    data: Array<OAuthGrantAdminResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

