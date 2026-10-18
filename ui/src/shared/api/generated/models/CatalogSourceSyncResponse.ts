/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Acknowledgement of a requested sync; the sync runs in the background.
 */
export type CatalogSourceSyncResponse = {
    /**
     * Earliest time the sync may start (a run in progress finishes first); null once the sync is already wanted.
     */
    due_at: (string | null);
    /**
     * The sync overrides the mass-removal guard.
     */
    forced: boolean;
    /**
     * Source slug.
     */
    slug: string;
    /**
     * `queued`: this call requested the sync; `coalesced`: it joined a sync already pending.
     */
    status: string;
};

