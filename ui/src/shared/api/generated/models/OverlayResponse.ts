/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OverlayLinksResponse } from './OverlayLinksResponse';
/**
 * Full overlay resource response.
 */
export type OverlayResponse = {
    _links: OverlayLinksResponse;
    api_id: string;
    confirmed_at: (string | null);
    confirmed_by_execution_id: (string | null);
    confirmed_revision_id?: (string | null);
    contributed_by: (string | null);
    created_at: string;
    created_by?: (string | null);
    deprecated_at: (string | null);
    deprecated_reason?: (string | null);
    document: Record<string, any>;
    id: string;
    status: string;
    superseded_revision_id?: (string | null);
    target_revision_id: (string | null);
    updated_at: (string | null);
};

