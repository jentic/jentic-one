/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiRevisionResponse } from './ApiRevisionResponse';
/**
 * Cursor-paginated list of API revisions.
 */
export type ApiRevisionListResponse = {
    data: Array<ApiRevisionResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

