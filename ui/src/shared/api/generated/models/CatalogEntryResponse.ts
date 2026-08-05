/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CatalogEntryLinksResponse } from './CatalogEntryLinksResponse';
/**
 * A single browsable catalog entry.
 */
export type CatalogEntryResponse = {
    _links: CatalogEntryLinksResponse;
    /**
     * Catalog identity of the API (manifest domain, e.g. `stripe.com`).
     */
    api_id: string;
    /**
     * Manifest path of the entry within the public-APIs repo.
     */
    path: (string | null);
    /**
     * Whether this entry is already imported locally — its `spec_url` backs a non-archived revision in `GET /apis`.
     */
    registered: boolean;
    /**
     * Fetchable OpenAPI spec URL the entry resolves to (used for import + coverage).
     */
    spec_url: (string | null);
    /**
     * Whether this (registered) entry has an upstream spec update the local revision hasn't adopted yet. Always false for unregistered entries.
     */
    update_available?: boolean;
    /**
     * Registrable-domain vendor derived from `api_id` (e.g. `stripe.com`).
     */
    vendor: (string | null);
};

