/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OAuthAppRegistrationResponse } from './OAuthAppRegistrationResponse';
/**
 * Paginated list of OAuth app registrations.
 */
export type OAuthAppRegistrationListResponse = {
    data: Array<OAuthAppRegistrationResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

