/**
 * Workspace module — UI-facing types.
 *
 * The committed generated client (`@/shared/api`) types most `/apis` responses
 * as `any` (the committed `ui/openapi.json` predates the FastAPI-generated spec
 * with named list/operation/revision schemas — see STATUS.md "Codegen
 * divergence"). Rather than regenerate on a feature branch (which re-buckets
 * the whole foundation client and breaks `shared/auth`), this module types the
 * envelopes here, derived from the **real** wire shapes verified against the
 * running backend on :8000. The repository tier (`client.ts`) is the single
 * place that casts the generated `any` into these shapes.
 *
 * Scope: APIs only. Workflows, credentials, and toolkits belong to other
 * modules and are intentionally absent here.
 */

/** The `(vendor, name, version)` identity triple, plus derived host. */
export interface ApiRef {
	vendor: string;
	name: string;
	version: string;
	host: string | null;
}

/** Generic cursor-paginated envelope shared by every `/apis` list endpoint. */
export interface CursorPage<T> {
	items: T[];
	hasMore: boolean;
	nextCursor: string | null;
}

/**
 * A workspace API row (from `GET /apis` and `GET /apis/{v}/{n}/{ver}`).
 *
 * `source` / `registered` are optional: they exist on the live (catalog-era)
 * backend but NOT in this branch's committed `ApiResponse` model. Treating them
 * as optional lets the UI read them when present without depending on the
 * catalog rebase having landed. Workspace shows local APIs either way.
 */
export interface WorkspaceApi {
	api: ApiRef;
	/** Catalog identity slug (`domain[/sub-api]`) recorded at import, when any —
	 * the preferred friendly-title source. */
	catalogApiId: string | null;
	displayName: string | null;
	description: string | null;
	iconUrl: string | null;
	currentRevisionId: string | null;
	revisionCount: number;
	operationCount: number;
	securitySchemes: string[];
	source?: string;
	registered?: boolean;
	/**
	 * Provenance of the current revision (Flow-3): `catalog` (imported from the
	 * public catalog), `overlay` (materialized from a confirmed overlay), or null
	 * (manual import). Optional — only present once the Flow-3 backend fields land;
	 * gates the one-click Re-import (safe only for `catalog` origin).
	 */
	origin?: string | null;
	/** Upstream spec URL backing the current revision, when known (catalog linkage). */
	sourceUrl?: string | null;
	/**
	 * Whether the upstream spec at `sourceUrl` has a notified update this API
	 * hasn't adopted yet (Flow-3). Re-importing clears it.
	 */
	updateAvailable?: boolean;
	createdAt: string;
	updatedAt: string;
}

/** One operation in an API's current (live) revision. */
export interface ApiOperation {
	operationId: string;
	method: string;
	path: string;
	name: string | null;
	description: string | null;
	tags: string[];
	deprecated: boolean;
	revisionId: string;
}

/** Lifecycle state of a revision (wire `StrEnum` serialized as a string). */
export type RevisionState = 'draft' | 'imported' | 'published' | 'archived' | (string & {});

/**
 * Provenance of a revision (wire `RevisionOrigin`): `overlay` (materialized
 * from a confirmed overlay), `catalog` (imported from the public catalog),
 * `uploaded` (user-supplied spec), or null (plain import).
 */
export type RevisionOrigin = 'overlay' | 'catalog' | 'uploaded' | (string & {});

/** A single revision of an API (from `GET /apis/{…}/revisions`). */
export interface ApiRevision {
	revisionId: string;
	api: ApiRef;
	sourceType: string;
	sourceUrl: string | null;
	specDigest: string;
	operationCount: number;
	state: RevisionState;
	/** Provenance marker; null for a plain import (see {@link RevisionOrigin}). */
	origin: RevisionOrigin | null;
	/** The principal that submitted the spec this revision was ingested from. */
	submittedBy: string | null;
	isCurrent: boolean;
	promotedAt: string | null;
	archivedAt: string | null;
	createdAt: string;
	/** Action links from `_links`; null when the action isn't offered. */
	promoteHref: string | null;
	archiveHref: string | null;
}

/** Lifecycle status of an overlay (wire `StrEnum` serialized as a string). */
export type OverlayStatus = 'pending' | 'confirmed' | 'deprecated' | (string & {});

/**
 * A single overlay on an API (from `GET /apis/{…}/overlays` and
 * `GET /apis/{…}/overlays/{id}`).
 *
 * The overlay list/get responses are typed `any` in the generated client, so
 * the repository casts the raw JSON into this shape via `toOverlay` (mirroring
 * how `toWorkspaceApi` re-types the `any` `/apis` payload).
 */
export interface Overlay {
	id: string;
	status: OverlayStatus;
	/**
	 * The authenticated principal that submitted the overlay (`identity.sub`,
	 * e.g. `usr_…`) — resolve to a friendly name via `ActorLabel`. Distinct from
	 * {@link contributedBy}, the free-text attribution a client may send.
	 */
	createdBy: string | null;
	/** Free-text attribution supplied in the submit body (e.g. a skill name). */
	contributedBy: string | null;
	/**
	 * The raw OpenAPI Overlay document (`{overlay, info, actions[]}`), used to
	 * derive the human-readable action summary. Typed `unknown` — always read it
	 * through `summarizeOverlayActions`.
	 */
	document: unknown;
	createdAt: string;
	confirmedAt: string | null;
	deprecatedAt: string | null;
	/**
	 * Why the overlay was deprecated — `manual` / `rollback` /
	 * `superseded_by_reimport` — persisted at the moment of deprecation so the
	 * historical event keeps its verb (a rollback stays "rolled back") instead
	 * of being re-derived from the API's moving current-revision pointer. Null
	 * when not deprecated, or for rows deprecated before the field existed.
	 */
	deprecatedReason: string | null;
	targetRevisionId: string | null;
	confirmedRevisionId: string | null;
	/**
	 * The revision this overlay superseded when it materialized (its rollback
	 * target). Non-null only after a confirm; when it equals the API's current
	 * revision again, the overlay was rolled back.
	 */
	supersededRevisionId: string | null;
	/**
	 * Action links from `_links`; null when the action isn't valid for the
	 * overlay's current status. Backend advertises these state-validity links
	 * (mirroring revisions' promote/archive), so a surface renders an action only
	 * when the backend offers it. The `overlays:confirm` permission gate on
	 * confirm/rollback is still enforced server-side (403).
	 */
	confirmHref: string | null;
	rollbackHref: string | null;
	deprecateHref: string | null;
}

/** Result of enqueuing an import (`POST /apis` → 202). */
export interface ImportJob {
	jobId: string;
	status: string;
}

/** Terminal/intermediate job state when polling `/jobs/{id}`. */
export interface JobStatus {
	jobId: string;
	status: string;
	error: string | null;
}

/** A single import source for the import dialog. */
export type ImportSource =
	| { type: 'url'; url: string; vendor?: string; apiName?: string; version?: string }
	| { type: 'inline'; content: string; filename: string };
