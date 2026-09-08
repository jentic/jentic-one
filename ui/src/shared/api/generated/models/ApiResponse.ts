/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiLinksResponse } from './ApiLinksResponse';
import type { ApiReferenceResponse } from './ApiReferenceResponse';
/**
 * Full API aggregate response.
 *
 * ``GET /apis`` is the local registry — every item is an API imported into
 * this deployment. The public catalog of importable-but-not-yet-imported APIs
 * is a separate surface (``GET /catalog``); the two are not blended.
 */
export type ApiResponse = {
    _links: ApiLinksResponse;
    api: ApiReferenceResponse;
    catalog_api_id: (string | null);
    created_at: string;
    current_revision_id: (string | null);
    description: (string | null);
    display_name: (string | null);
    icon_url: (string | null);
    operation_count: number;
    /**
     * Provenance of the current revision: `catalog` (imported from the public catalog), `overlay` (materialized from a confirmed overlay), or null (manual import).
     */
    origin?: (string | null);
    revision_count: number;
    security_schemes: Array<string>;
    /**
     * Upstream spec URL backing the current revision, when known (catalog linkage).
     */
    source_url?: (string | null);
    /**
     * Whether the upstream spec at `source_url` has a notified update this API hasn't adopted yet (Flow-3). Re-importing clears it.
     */
    update_available?: boolean;
    updated_at: string;
};

