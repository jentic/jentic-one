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
    /**
     * ID of the API the overlay applies to.
     */
    api_id: string;
    /**
     * When the overlay was confirmed (RFC 3339); null if not confirmed.
     */
    confirmed_at: (string | null);
    /**
     * ID of the execution supplied when the overlay was confirmed; null if none.
     */
    confirmed_by_execution_id: (string | null);
    /**
     * ID of the revision produced by applying the overlay; null until confirmation completes.
     */
    confirmed_revision_id?: (string | null);
    /**
     * Free-text attribution label supplied at submission; null if none.
     */
    contributed_by: (string | null);
    /**
     * When the overlay was created (RFC 3339).
     */
    created_at: string;
    /**
     * ID of the actor that created the overlay.
     */
    created_by?: (string | null);
    /**
     * When the overlay was deprecated (RFC 3339); null if not deprecated.
     */
    deprecated_at: (string | null);
    /**
     * Why the overlay was deprecated: `manual`, `rollback` or `superseded_by_reimport`; null when not deprecated or not recorded.
     */
    deprecated_reason?: (string | null);
    /**
     * The OpenAPI Overlay document.
     */
    document: Record<string, any>;
    /**
     * Unique ID of the overlay.
     */
    id: string;
    /**
     * Lifecycle status of the overlay: `pending`, `confirmed` or `deprecated`.
     */
    status: string;
    /**
     * ID of the revision the overlay replaced when applied, restored by a rollback; null if none.
     */
    superseded_revision_id?: (string | null);
    /**
     * ID of the revision the overlay was written against; null if unpinned.
     */
    target_revision_id: (string | null);
    /**
     * When the overlay was last updated (RFC 3339); null if never updated.
     */
    updated_at: (string | null);
};

