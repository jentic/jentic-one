/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Hypermedia links for an overlay resource.
 *
 * Links are advertised based on the overlay's *state validity* (mirroring the
 * revisions resource's promote/archive links), so a surface renders an action only
 * when it is applicable to the current status. They are not permission-scoped — the
 * ``overlays:confirm`` gate on confirm/rollback is still enforced server-side (403).
 */
export type OverlayLinksResponse = {
    /**
     * URL of the API the overlay applies to.
     */
    api: string;
    /**
     * URL to confirm the overlay; null unless it is `pending`.
     */
    confirm?: (string | null);
    /**
     * URL to deprecate the overlay (via `DELETE`); null once it is `deprecated`.
     */
    deprecate?: (string | null);
    /**
     * URL to roll back the overlay; null unless it is `confirmed` and its revision has been produced.
     */
    rollback?: (string | null);
    /**
     * URL of this overlay.
     */
    self: string;
};

