/**
 * Discover module — UI-facing types.
 *
 * Discover browses the catalog (`GET /catalog`): a single keyset-paginated feed
 * of importable APIs from every catalog source the caller can see, each carrying
 * a `registered` flag (true once it's been imported into this deployment). There is no server-side blend of local + catalog
 * — the catalog's own per-entry `registered` boolean is the source of truth for
 * the "In your workspace" marker, so the ledger is fed entirely from `/catalog`.
 *
 * Scope: there are no `workflow` / `endpoint` entity types — the catalog
 * backend exposes only API-level discovery.
 */

/**
 * A discoverable catalog API, normalized from a `GET /catalog` entry. Components
 * render only this shape.
 */
export interface DiscoveryEntity {
	/** Stable, unique row id (also the React key). Mirrors the catalog `entry_id`. */
	id: string;
	/**
	 * Catalog `entry_id` used to address the entry on the catalog routes
	 * (`/catalog/{entry_id}`, `/catalog/{entry_id}/operations`, `:import`).
	 */
	entryId: string;
	/** The entry's `catalog_api_id` (the identity workspace APIs record at import). */
	apiId: string;
	/** Slug of the catalog source the entry comes from (`public`, `internal`, …). */
	sourceSlug: string;
	/**
	 * The entry's position within its source: `entry_id` without the
	 * `{source_slug}/` prefix (`public/stripe.com` → `stripe.com`). The browse
	 * feed is ordered by `entry_id`, so within one source this is the order
	 * the rows arrive in — the ledger's letters and keyset jumps key on it.
	 */
	browseKey: string;
	/**
	 * Human-readable title, distinct per entry even within one umbrella vendor:
	 * the source's `title` when it lists one, else derived from the
	 * `catalog_api_id` — a sub-API segment (`nytimes.com/article_search` →
	 * `Article Search`) or the bare id when there is no sub-API.
	 */
	summary: string;
	/**
	 * Secondary line under the title — the display vendor (e.g.
	 * `nytimes.com`). Present only when it adds context beyond `summary`
	 * (i.e. the entry is an umbrella sub-API); omitted otherwise.
	 */
	subtitle?: string;
	/**
	 * The vendor the ledger groups the entry under (`display_vendor`, e.g.
	 * `stripe.com` for a namespaced `aws-prod~stripe.com`). Display only — never
	 * an identity.
	 */
	displayVendor: string;
	/**
	 * How many other visible entries (from other sources) share this entry's
	 * `catalog_api_id` and are collapsed into it.
	 */
	alternates: number;
	/** Whether the entry has an importable spec (false for a listing-only card). */
	hasSpec: boolean;
	/** Whether the API is already imported locally. Drives the In your workspace / Available pill. */
	registered: boolean;
	/**
	 * Whether this (registered) entry has an upstream spec update the local
	 * revision hasn't adopted yet. Always false for unregistered entries — drives
	 * the "Update available" badge.
	 */
	updateAvailable: boolean;
	/** Vendor / domain key (e.g. `stripe.com`) used for the vendor icon. */
	vendor?: string;
	/**
	 * The catalog entry's identity `vendor`, verbatim (no fallback) — what a
	 * catalog import registers the API under (`catalogImportRef`). Absent when
	 * the source lists none.
	 */
	catalogVendor?: string;
	/** The entry's version, when its source lists one. */
	version?: string;
	/** The entry's human-facing page at its source (a GitHub tree for `public`), when known. */
	humanUrl?: string;
}

/**
 * The registration filter the toolbar exposes. Maps onto the catalog query
 * params: `all` sends no flag, `unregistered` → `unregistered_only`,
 * `outdated` → `outdated_only` (registered entries with an upstream update
 * available).
 */
export type CatalogFilter = 'all' | 'unregistered' | 'outdated';
