/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OverlayResponse } from './OverlayResponse';
/**
 * Cursor-paginated list of overlays.
 */
export type OverlayListResponse = {
    data: Array<OverlayResponse>;
    has_more: boolean;
    next_cursor?: (string | null);
};

