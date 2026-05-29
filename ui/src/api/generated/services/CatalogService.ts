/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiListPage } from '../models/ApiListPage';
import type { ApiOut } from '../models/ApiOut';
import type { ImportOut } from '../models/ImportOut';
import type { ImportRequest } from '../models/ImportRequest';
import type { NoteCreate } from '../models/NoteCreate';
import type { OperationListPage } from '../models/OperationListPage';
import type { OverlaySubmit } from '../models/OverlaySubmit';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CatalogService {
    /**
     * List APIs — browse all available API providers (local and catalog)
     * Returns paginated list of API providers — both locally registered and from the Jentic public catalog.
     *
     * Every entry has:
     * - `source: "local"` — spec is indexed locally, operations are searchable and executable
     * - `source: "catalog"` — available from the Jentic public catalog; add credentials to use
     * - `has_credentials: bool` — whether credentials have been configured for this API
     * - `has_workflows: bool` — only on catalog rows; `true` when the public catalog
     * also ships Arazzo workflows for this vendor (renders as a `+ workflows` chip
     * in the UI). Always `false` / omitted on local rows since those workflows are
     * already imported and listed under `GET /workflows`.
     *
     * Use `?source=local` or `?source=catalog` to filter. Default returns all.
     * To use a catalog API: call `POST /credentials` with `api_id` set — the spec is imported automatically.
     * @returns ApiListPage Successful Response
     * @throws ApiError
     */
    public static listApisApisGet({
        page = 1,
        limit = 20,
        source,
        q,
        includeImported = false,
    }: {
        /**
         * Page number (1-indexed)
         */
        page?: number,
        /**
         * Results per page
         */
        limit?: number,
        /**
         * Filter by source: `local` (locally registered) or `catalog` (public catalog, not yet configured). Default: all.
         */
        source?: (string | null),
        /**
         * Substring filter on API id/name
         */
        q?: (string | null),
        /**
         * When `source=catalog`, controls whether catalog entries that have already been imported into the local workspace are still returned. Default `false` preserves the historical 'things you don't have yet' behaviour used by the workspace 'From the catalog' section. The `/discover` UI sets this to `true` so users keep seeing the full Jentic public catalog after importing — registered entries surface with `source: local` and a `Ready` / `Credential expired` pill instead of vanishing. No-op when `source != catalog`.
         */
        includeImported?: boolean,
    }): CancelablePromise<ApiListPage> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis',
            query: {
                'page': page,
                'limit': limit,
                'source': source,
                'q': q,
                'include_imported': includeImported,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get API details — metadata, auth schemes, servers, and optional spec sections
     * Returns API metadata enriched with selected OpenAPI spec sections.
     *
     * **Default response** (no `?sections=`) includes:
     * - Summary fields: id, name, vendor, description, base_url, operation_count, overlay_count
     * - `info` — title, version, contact, license, terms of service
     * - `servers` — base URLs and variables (merged from spec + confirmed overlays)
     * - `security_schemes` — security scheme definitions (merged from spec + confirmed overlays),
     * plus `security_required` (global security requirements)
     * - `credentials_configured` — list of auth_types that already have a credential bound.
     * Use this to build a credential-setup UI: iterate `security_schemes`, check each key
     * against `security_schemes` (each scheme has a `type` field) to determine which auth types need credentials.
     * to fill in the required fields and POST to `/credentials`.
     *
     * **Credential setup flow:**
     * 1. Call `GET /apis/{api_id}` — inspect `security_schemes` and `credentials_configured`
     * 2. For each unconfigured scheme, determine required fields from the scheme type:
     * - `http bearer` → `secret` (token)
     * - `http basic` → `secret` (password) + optional `identity` (username)
     * - `apiKey` → `secret` (key value); if compound, check scheme names for Secret/Identity
     * 3. Prompt user for values, then `POST /credentials` with `api_id`, `auth_type`, `value` (and `identity` if needed).
     * 4. Verify with `GET /credentials?api_id={api_id}`
     *
     * **Optional sections** (add via `?sections=`):
     * - `tags` — tag objects with names and descriptions
     * - `paths` — full paths object (can be very large — prefer GET /apis/{api_id}/operations)
     * - `components` — all reusable component definitions (schemas, parameters, responses, etc.)
     * - `webhooks` — OpenAPI 3.1 webhooks (if present)
     *
     * **Full spec download:** `GET /apis/{api_id}/openapi.json`
     * @returns ApiOut API detail — format controlled by Accept header.
     * @throws ApiError
     */
    public static getApiApisApiIdGet({
        apiId,
        sections,
    }: {
        /**
         * API ID (hostname or hostname/path format)
         */
        apiId: string,
        /**
         * Comma-separated list of OpenAPI spec sections to include in the response. Valid values: components, info, paths, security, servers, tags, webhooks. Default (when omitted): info, security, servers. Large sections (paths, components, webhooks) must be requested explicitly. Use GET /apis/{api_id}/openapi.json to download the full merged spec.
         */
        sections?: (string | null),
    }): CancelablePromise<ApiOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{api_id}',
            path: {
                'api_id': apiId,
            },
            query: {
                'sections': sections,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove an API from the workspace
     * Remove an API and its single-API workflows from the workspace.
     *
     * By default credentials are preserved (api_id reference kept intact) so they
     * automatically re-link if the API is re-imported later. Toolkit bindings also
     * survive. Pass `cascade=true` to also delete all credentials and their
     * toolkit bindings for a clean slate.
     * @returns void
     * @throws ApiError
     */
    public static deleteApiApisApiIdDelete({
        apiId,
        cascade = false,
    }: {
        /**
         * API id to delete, e.g. api.elevenlabs.io
         */
        apiId: string,
        /**
         * If true, also delete credentials bound to this API
         */
        cascade?: boolean,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/apis/{api_id}',
            path: {
                'api_id': apiId,
            },
            query: {
                'cascade': cascade,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Download merged OpenAPI spec as JSON — base spec with all confirmed overlays applied
     * Returns the full merged OpenAPI spec for this API as a JSON download.
     *
     * All confirmed overlays are applied on top of the base spec using deep merge
     * (overlay values win on conflict). Pending overlays are not included.
     *
     * Overlay actions with `target: "$"` are applied as root-level deep merges.
     * Actions targeting specific paths or operations are listed in
     * `x-jentic-unapplied-overlays` for transparency.
     *
     * For selective access to spec sections without downloading the full file,
     * use `GET /apis/{api_id}?sections=info,servers,security,tags`.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getApiOpenapiJsonApisApiIdOpenapiJsonGet({
        apiId,
    }: {
        /**
         * API ID (hostname or hostname/path format)
         */
        apiId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{api_id}/openapi.json',
            path: {
                'api_id': apiId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Download merged OpenAPI spec as YAML — base spec with all confirmed overlays applied
     * Returns the full merged OpenAPI spec for this API as a YAML download.
     *
     * All confirmed overlays are applied on top of the base spec using deep merge
     * (overlay values win on conflict). Pending overlays are not included.
     *
     * Overlay actions with `target: "$"` are applied as root-level deep merges.
     * Actions targeting specific paths or operations are listed in
     * `x-jentic-unapplied-overlays` for transparency.
     *
     * For selective access to spec sections without downloading the full file,
     * use `GET /apis/{api_id}?sections=info,servers,security,tags`.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getApiOpenapiYamlApisApiIdOpenapiYamlGet({
        apiId,
    }: {
        /**
         * API ID (hostname or hostname/path format)
         */
        apiId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{api_id}/openapi.yaml',
            path: {
                'api_id': apiId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List operations for an API — enumerate all available actions
     * Returns paginated list of operations for the given API. Each item has capability id, summary, description and OpenAPI tags. Use GET /inspect/{id} for full schema.
     * @returns OperationListPage Operation list — format controlled by Accept header.
     * @throws ApiError
     */
    public static listApiOperationsApisApiIdOperationsGet({
        apiId,
        page = 1,
        limit = 50,
        offset,
        tag,
    }: {
        /**
         * API ID to list operations for
         */
        apiId: string,
        /**
         * Page number (1-indexed)
         */
        page?: number,
        /**
         * Results per page
         */
        limit?: number,
        /**
         * Skip N operations (0-indexed). When provided, takes precedence over `page` for cursor-style pagination — pass `offset=N&limit=M` to grab an arbitrary window from the Detail Sheet's load-more affordance.
         */
        offset?: (number | null),
        /**
         * Case-insensitive substring filter on the operation's OpenAPI `tags[]`. Tags are projected from the spec at request time. `total` reflects the post-filter count so the page envelope stays consistent.
         */
        tag?: (string | null),
    }): CancelablePromise<OperationListPage> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{api_id}/operations',
            path: {
                'api_id': apiId,
            },
            query: {
                'page': page,
                'limit': limit,
                'offset': offset,
                'tag': tag,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Submit an OpenAPI overlay — patch the stored spec for this API
     * Submit an OpenAPI Overlay 1.0 document to patch the stored spec for this API.
     *
     * Overlays are additive and ordered — later overlays override matching keys from
     * earlier ones via merge. A new overlay starts as **pending** and is
     * auto-confirmed the first time a broker call for this API succeeds.
     *
     * See the `overlay` field schema for structure, common targets, and security
     * scheme examples including compound apiKey schemes (Discourse-style).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static submitOverlayApisApiIdOverlaysPost({
        apiId,
        requestBody,
    }: {
        /**
         * API ID to submit overlay for
         */
        apiId: string,
        /**
         * OpenAPI Overlay 1.0 document to patch the stored spec — adds security schemes, corrects base URLs, or enriches operation metadata
         */
        requestBody: OverlaySubmit,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{api_id}/overlays',
            path: {
                'api_id': apiId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List overlays for an API — returns full overlay documents
     * Return all overlays for an API, each with its full overlay document included.
     *
     * Confirmed overlays are listed first, then pending, both ordered by creation date
     * descending. Each overlay includes the complete OpenAPI Overlay 1.0 document so
     * clients don't need a second call to inspect the overlay content.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listOverlaysApisApiIdOverlaysGet({
        apiId,
    }: {
        /**
         * API ID to list overlays for
         */
        apiId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{api_id}/overlays',
            path: {
                'api_id': apiId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete an overlay
     * Delete an overlay by ID.
     *
     * Permanently removes the overlay from the database. Works on both pending and confirmed
     * overlays. If the overlay was confirmed and actively patching the spec, the next
     * broker call will use the spec without this overlay's changes.
     *
     * Parameters:
     * api_id: API ID that owns this overlay
     * overlay_id: Overlay ID to delete (format: overlay_xxxxxxxx)
     *
     * Returns:
     * Confirmation with deleted overlay_id and api_id.
     *
     * Use when an overlay was submitted incorrectly or is no longer needed. To replace
     * an incorrect overlay, delete it first, then submit a corrected version.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static deleteOverlayApisApiIdOverlaysOverlayIdDelete({
        apiId,
        overlayId,
    }: {
        /**
         * API ID
         */
        apiId: string,
        /**
         * Overlay ID to delete
         */
        overlayId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/apis/{api_id}/overlays/{overlay_id}',
            path: {
                'api_id': apiId,
                'overlay_id': overlayId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List the public API catalog
     * Returns entries from the cached public API catalog manifest.
     * Use ``POST /catalog/refresh`` to sync from GitHub first if the list is empty.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listCatalogCatalogGet({
        q,
        limit = 50,
        registeredOnly = false,
        unregisteredOnly = false,
    }: {
        /**
         * Search term to filter APIs by name or description
         */
        q?: (string | null),
        /**
         * Maximum number of results (1-500)
         */
        limit?: number,
        /**
         * Return only APIs already registered locally
         */
        registeredOnly?: boolean,
        /**
         * Return only APIs not yet registered locally
         */
        unregisteredOnly?: boolean,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog',
            query: {
                'q': q,
                'limit': limit,
                'registered_only': registeredOnly,
                'unregistered_only': unregisteredOnly,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Refresh the API catalog manifest from GitHub
     * Rebuilds the internal catalog manifest from the jentic/jentic-public-apis repository.
     * The manifest is used by lazy import — when you `POST /credentials` for an API not yet in
     * your local registry, Jentic Mini resolves the spec from this manifest automatically.
     *
     * Fetches the curated apis.json index and the workflows directory listing
     * (two unauthenticated HTTP requests). Safe to call repeatedly.
     * The manifest auto-refreshes daily; only call this explicitly if you need immediate sync
     * after a new API has been added to the public catalog.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static refreshCatalogCatalogRefreshPost(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/catalog/refresh',
        });
    }
    /**
     * Get a catalog entry with spec location
     * Return details for a single catalog API, including the spec download URL.
     *
     * Use the returned `spec_url` with `POST /import` to import this API:
     *
     * POST /import
     * {"sources": [{"type": "url", "url": "<spec_url>", "force_api_id": "<api_id>"}]}
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getCatalogEntryCatalogApiIdGet({
        apiId,
    }: {
        /**
         * API ID from catalog to retrieve
         */
        apiId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/{api_id}',
            path: {
                'api_id': apiId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview operations for a catalog API without importing
     * Server-side spec fetch + parse for the directory API preview.
     *
     * Why this exists: the Detail Sheet wants to show the operation table for a
     * directory API without committing to a full import. Doing it server-side is
     * the only sane option — fetching the raw GitHub spec from the browser hits
     * CORS, plus we already have urllib + yaml plumbing here.
     *
     * Returns the same `{data, total}` envelope as `GET /apis/{id}/operations`
     * so the UI can reuse the same renderer for both workspace and directory
     * APIs. Capped at `_PREVIEW_MAX_OPERATIONS` for huge specs (stripe-style).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static previewCatalogOperationsCatalogApiIdOperationsGet({
        apiId,
        offset,
        limit = 200,
        tag,
    }: {
        /**
         * Catalog api_id to preview operations for
         */
        apiId: string,
        /**
         * Number of operations to skip (pagination).
         */
        offset?: number,
        /**
         * Maximum operations to return after applying `offset`. The hard ceiling is 200; combined with `offset` it powers cheap load-more pagination from the Detail Sheet.
         */
        limit?: number,
        /**
         * Case-insensitive substring filter on `op.tags[]`. Filtering happens *before* counting, so `total` reflects the post-filter operation count and `truncated` is computed against the filtered list.
         */
        tag?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/{api_id}/operations',
            path: {
                'api_id': apiId,
            },
            query: {
                'offset': offset,
                'limit': limit,
                'tag': tag,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Preview workflows for a catalog API without importing
     * Server-side Arazzo fetch + parse for the directory workflow preview.
     *
     * Returns one row per workflow inside `workflows.arazzo.json` with just
     * enough metadata to render the API Detail Sheet's Workflows section
     * (workflow id, recomputed slug, summary, description, steps count).
     *
     * Empty-list response (rather than 404) when the api_id has no
     * workflow manifest entry — keeps the UI rendering path uniform: the
     * sheet always asks, sometimes the answer is "none".
     * @returns any Successful Response
     * @throws ApiError
     */
    public static previewCatalogWorkflowsCatalogApiIdWorkflowsGet({
        apiId,
    }: {
        /**
         * Catalog api_id to preview workflows for
         */
        apiId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/catalog/{api_id}/workflows',
            path: {
                'api_id': apiId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Import an API spec or workflow — add to the searchable catalog
     * Registers an OpenAPI spec or Arazzo workflow into the catalog and BM25 index.
     * Source types: path (local file), url (fetch from URL), inline (spec content in request body).
     * For OpenAPI specs: parses operations, computes capability IDs, indexes descriptions.
     * For Arazzo workflows: stores definition, extracts input schema and involved APIs.
     * Returns the registered API or workflow with its canonical id.
     * @returns ImportOut Successful Response
     * @throws ApiError
     */
    public static importSourcesImportPost({
        requestBody,
    }: {
        /**
         * Array of import sources (local file paths, URLs, or inline spec content) to register in the catalog — supports OpenAPI 3.x and Arazzo 1.0
         */
        requestBody: ImportRequest,
    }): CancelablePromise<ImportOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/import',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add a note — annotate a capability with feedback or a correction
     * Attaches a note to any capability (operation, workflow, or API). Use to report auth corrections, schema errors, or updated Arazzo workflows. Notes feed back into the catalog improvement loop.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static createNoteNotesPost({
        requestBody,
    }: {
        /**
         * Note details: resource ID, note type (auth_quirk/usage_hint/execution_feedback/correction), content, optional execution link, confidence level, and source
         */
        requestBody: NoteCreate,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/notes',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List notes for a resource
     * List notes attached to resources (operations, workflows, APIs).
     *
     * Notes capture observations from execution — success signals, failure patterns,
     * data validation findings, and human annotations. Agents use notes to build
     * operational knowledge and improve reliability over time.
     *
     * Filter by `?resource={id}` to see notes for a specific operation/workflow,
     * or by `?type={type}` to filter by note category (e.g., "success", "error", "validation").
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listNotesNotesGet({
        resource,
        type,
        limit = 50,
    }: {
        /**
         * Filter notes by resource ID (capability_id, api_id, or workflow slug)
         */
        resource?: (string | null),
        /**
         * Filter notes by type (auth_quirk, usage_hint, execution_feedback, correction)
         */
        type?: (string | null),
        /**
         * Maximum number of notes to return (1-500)
         */
        limit?: number,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/notes',
            query: {
                'resource': resource,
                'type': type,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete a note
     * Permanently delete a note.
     *
     * Use this to remove outdated observations, incorrect annotations, or
     * notes that no longer apply after an API change.
     * @returns void
     * @throws ApiError
     */
    public static deleteNoteNotesNoteIdDelete({
        noteId,
    }: {
        /**
         * Note ID to delete (format: note_{8chars})
         */
        noteId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/notes/{note_id}',
            path: {
                'note_id': noteId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List workflows — browse available multi-step Arazzo workflows
     * Returns registered workflows (source: local) plus available catalog workflow sources
     * (source: catalog) — APIs in the Jentic public catalog that have associated workflows.
     *
     * Catalog entries show the API they belong to; add credentials to auto-import their workflows.
     * Use ?source=local or ?source=catalog to filter. Default returns all.
     *
     * Pass `page` + `limit` for a `{data, total, page, limit, total_pages}` envelope; omit
     * both to keep the original bare-list response (workspace tiles still work the old way).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listWorkflowsWorkflowsGet({
        page,
        limit,
        q,
        source,
    }: {
        /**
         * Page number (1-indexed). When supplied alongside `limit`, the response switches from a bare list to a `{data, total, page, limit, total_pages}` envelope. Default (omitted) returns the unpaginated list for backward compatibility with existing callers.
         */
        page?: (number | null),
        /**
         * Page size when paginating. Triggers the paginated envelope shape — see `page`. Omit both to keep the historical bare-list behaviour.
         */
        limit?: (number | null),
        /**
         * Filter by name or API, e.g. "stripe" or "oauth"
         */
        q?: (string | null),
        /**
         * Filter by source: "local" or "catalog"
         */
        source?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workflows',
            query: {
                'page': page,
                'limit': limit,
                'q': q,
                'source': source,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get workflow definition — Arazzo spec and input schema
     * Returns the workflow definition with content negotiation:
     * - application/json (default): workflow metadata with simplified step info
     * - application/vnd.oai.workflows+json: raw Arazzo document as JSON
     * - application/vnd.oai.workflows+yaml: raw Arazzo document as YAML
     * - text/markdown: compact LLM-friendly summary with input schema and steps
     * - text/html: human-readable HTML summary
     * Execute via broker: POST /{jentic_host}/workflows/{slug}
     * @returns any Workflow definition — format controlled by Accept header.
     * @throws ApiError
     */
    public static getWorkflowWorkflowsSlugGet({
        slug,
    }: {
        /**
         * Workflow slug (URL-safe identifier)
         */
        slug: string,
    }): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/workflows/{slug}',
            path: {
                'slug': slug,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete a workflow from the workspace
     * Permanently delete a workflow and its Arazzo file from the workspace.
     * @returns void
     * @throws ApiError
     */
    public static deleteWorkflowWorkflowsSlugDelete({
        slug,
    }: {
        /**
         * Workflow slug to delete
         */
        slug: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/workflows/{slug}',
            path: {
                'slug': slug,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
