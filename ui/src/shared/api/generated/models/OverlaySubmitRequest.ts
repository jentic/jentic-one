/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Payload for submitting a new overlay.
 */
export type OverlaySubmitRequest = {
    /**
     * Free-text attribution label for who contributed the overlay.
     */
    contributed_by?: (string | null);
    /**
     * OpenAPI Overlay document; must be a JSON object with an `actions` array.
     */
    document: Record<string, any>;
    /**
     * ID of the revision the overlay was written against; null to leave unpinned.
     */
    target_revision_id?: (string | null);
};

