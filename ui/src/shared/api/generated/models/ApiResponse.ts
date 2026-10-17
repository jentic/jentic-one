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
    /**
     * Catalog identity the API was imported from (`domain[/sub-api]`, for example `nytimes.com/article_search`); null for manual imports.
     */
    catalog_api_id: (string | null);
    /**
     * When the API was created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the revision the API currently serves; null when it has no live revision.
     */
    current_revision_id: (string | null);
    /**
     * Free-text description of the API; null if not set.
     */
    description: (string | null);
    /**
     * Human-readable name to show for the API; null if not set.
     */
    display_name: (string | null);
    /**
     * URL of an icon image for the API; null if not set.
     */
    icon_url: (string | null);
    /**
     * Number of operations the API exposes.
     */
    operation_count: number;
    /**
     * Provenance of the current revision: `catalog` (imported from the public catalog), `overlay` (materialized from a confirmed overlay), or null (manual import).
     */
    origin?: (string | null);
    /**
     * Number of revisions recorded for the API.
     */
    revision_count: number;
    /**
     * Distinct security scheme types declared by the current revision.
     */
    security_schemes: Array<string>;
    /**
     * Upstream spec URL backing the current revision, when known (catalog linkage).
     */
    source_url?: (string | null);
    /**
     * Whether the upstream spec at `source_url` has a notified update this API hasn't adopted yet (Flow-3). Re-importing clears it.
     */
    update_available?: boolean;
    /**
     * When the API was last updated (RFC 3339); equals `created_at` if never updated.
     */
    updated_at: string;
};

