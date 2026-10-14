/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * POST /search request body.
 */
export type SearchRequest = {
    /**
     * Restrict results to these APIs. Each entry is a 'vendor[/name[/version]]' identifier of an imported API (e.g. 'github-com/api-github-com/1.1.4'); vendor and name are normalized like ingest, so raw spellings such as 'stripe.com/api' also resolve. The legacy colon-separated form 'vendor[:name[:version]]' is also accepted.
     */
    apis?: (Array<string> | null);
    /**
     * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
     */
    cursor?: (string | null);
    /**
     * Maximum number of results to return (1-100).
     */
    limit?: number;
    /**
     * Free-text search query; must not be empty.
     */
    query: string;
    /**
     * Pin specific APIs to a revision for this search. Keys are full 'vendor/name/version' identifiers (colon-separated also accepted); values are revision UUIDs.
     */
    revision_pins?: (Record<string, string> | null);
};

