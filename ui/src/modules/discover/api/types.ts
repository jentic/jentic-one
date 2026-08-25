/**
 * Discover module — UI-facing types.
 *
 * Discover browses the public catalog (`GET /catalog`): a single keyset-paginated
 * feed of importable APIs, each carrying a `registered` flag (true once it's been
 * imported into this deployment). There is no server-side blend of local + catalog
 * (the old `GET /apis?source=` shape was rejected under D-005a) — the catalog's own
 * per-entry `registered` boolean is the source of truth for the Imported/Available
 * badge, so the grid is fed entirely from `/catalog`.
 *
 * Scope note: unlike jentic-mini's Discover, this slice has no `workflow` /
 * `endpoint` entity types — the catalog backend exposes only API-level discovery.
 */

/**
 * A discoverable catalog API, normalized from a `GET /catalog` entry. Components
 * render only this shape; `raw` carries the original payload for detail panels
 * that need a field not surfaced here.
 */
export interface DiscoveryEntity {
	/** Stable, unique row id (also the React key). Mirrors the catalog `api_id`. */
	id: string;
	/**
	 * Catalog api_id used to address the entry on the catalog routes
	 * (`/catalog/{api_id}`, `/catalog/{api_id}/operations`, `/catalog/{api_id}:import`).
	 */
	apiId: string;
	/**
	 * Human-readable title, distinct per entry even within one umbrella vendor.
	 * Derived from `api_id`: a sub-API segment (`nytimes.com/article_search` →
	 * `Article Search`) or the bare `api_id` when there is no sub-API.
	 */
	summary: string;
	/**
	 * Secondary line under the title — the shared vendor/domain (e.g.
	 * `nytimes.com`). Present only when it adds context beyond `summary`
	 * (i.e. the entry is an umbrella sub-API); omitted otherwise.
	 */
	subtitle?: string;
	/** Whether the API is already imported locally. Drives the Imported/Available pill. */
	registered: boolean;
	/**
	 * Whether this (registered) entry has an upstream spec update the local
	 * revision hasn't adopted yet. Always false for unregistered entries — drives
	 * the "Update available" badge, mirroring the Workspace ApiCard signal.
	 */
	updateAvailable: boolean;
	/** Vendor / domain key (e.g. `stripe.com`) used for the vendor icon. */
	vendor?: string;
	/** GitHub source URL for the catalog spec, when the manifest has one. */
	githubUrl?: string;
	raw: unknown;
}

/**
 * The registration filter the toolbar exposes. Maps onto the catalog query
 * params: `all` sends neither flag, `registered` → `registered_only`,
 * `unregistered` → `unregistered_only`, `outdated` → `outdated_only` (registered
 * entries with an upstream update available).
 */
export type CatalogFilter = 'all' | 'registered' | 'unregistered' | 'outdated';
