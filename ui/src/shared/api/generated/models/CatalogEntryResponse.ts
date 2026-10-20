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
     * How many visible entries with the same `catalog_api_id` are collapsed into this one (see `_links.alternates`).
     */
    alternates?: number;
    /**
     * Canonical, cross-source API identity (`domain[/sub-api]`, or `{source_slug}~{id}` when namespaced). Credentials match on it.
     */
    catalog_api_id: string;
    /**
     * Vendor for grouping entries in a UI; never used for identity.
     */
    display_vendor?: (string | null);
    /**
     * Catalog entry identity, `{source_slug}/{external_id}`; the key every `/catalog/{entry_id}` route takes.
     */
    entry_id: string;
    /**
     * Whether the entry has an importable spec (false for a listing-only card).
     */
    has_spec?: boolean;
    /**
     * Human-facing page for the entry at its source, when known.
     */
    human_url?: (string | null);
    /**
     * Whether this entry is already imported into the local registry.
     */
    registered: boolean;
    /**
     * Slug of the catalog source the entry comes from.
     */
    source_slug: string;
    /**
     * Entry tags from the source.
     */
    tags?: Array<string>;
    /**
     * Entry title.
     */
    title?: (string | null);
    /**
     * Whether this (registered) entry has an upstream spec update the local revision hasn't adopted yet. Always false for unregistered entries.
     */
    update_available?: boolean;
    /**
     * Identity vendor derived from `catalog_api_id` (credentials match on it).
     */
    vendor: (string | null);
    /**
     * Entry version, when the source lists one.
     */
    version?: (string | null);
};

