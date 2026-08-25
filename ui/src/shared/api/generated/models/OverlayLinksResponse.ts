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
    api: string;
    confirm?: (string | null);
    deprecate?: (string | null);
    rollback?: (string | null);
    self: string;
};

