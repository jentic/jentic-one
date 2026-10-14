/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Hypermedia links for a (local) API resource.
 */
export type ApiLinksResponse = {
    /**
     * URL of the API's current revision; null when it has no live revision.
     */
    current_revision?: (string | null);
    /**
     * URL of the API's revision list.
     */
    revisions: string;
    /**
     * URL of this API.
     */
    self: string;
};

