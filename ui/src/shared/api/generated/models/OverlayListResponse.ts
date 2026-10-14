/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OverlayResponse } from './OverlayResponse';
/**
 * Cursor-paginated list of overlays.
 */
export type OverlayListResponse = {
    /**
     * The page of overlays.
     */
    data: Array<OverlayResponse>;
    /**
     * Whether more results exist after this page.
     */
    has_more: boolean;
    /**
     * Cursor for the next page; null when there are no more results.
     */
    next_cursor?: (string | null);
};

