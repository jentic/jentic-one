/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Payload for updating an existing overlay.
 */
export type OverlayUpdateRequest = {
    /**
     * Replacement OpenAPI Overlay document; omit to keep the current one.
     */
    document?: (Record<string, any> | null);
    /**
     * ID of the revision the overlay was written against.
     */
    target_revision_id?: (string | null);
};

