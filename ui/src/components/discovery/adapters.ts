import type { DiscoveryEntity } from './DiscoveryCard';
import { parseCapabilityId } from '@/lib/capabilityId';

// Server `source` is `'local' | 'catalog'`. UI vocab is `'workspace' | 'directory'`.
// Translate at this boundary so the rest of the page (and DiscoveryCard) speaks
// the UI language only.

export function serverSourceToUi(s: string | undefined): 'workspace' | 'directory' {
	return s === 'local' ? 'workspace' : 'directory';
}

export function apiToEntity(entry: any): DiscoveryEntity {
	const source = serverSourceToUi(entry.source);
	return {
		id: entry.id,
		type: 'api',
		source,
		summary: entry.name ?? entry.id,
		// Directory entries come from the catalog manifest which only has
		// `{api_id, path, sha, spec_url}` — no `info.description`. We used
		// to synthesise "Available in the Jentic public catalog. Add a
		// credential to import…" for layout parity, but every directory
		// card carrying the *same* string was actively misleading: it
		// looked like real metadata. A blank description column reads
		// cleaner and the `+ workflows` chip + action buttons carry the
		// differentiation the synthetic copy was pretending to provide.
		// Workspace descriptions (real `info.description` text) stay
		// untouched. Server-side description hydration is tracked in #437.
		description: entry.description ?? undefined,
		hasCredentials: Boolean(entry.has_credentials),
		// `/apis` populates `has_workflows` on catalog rows from the workflow
		// manifest (mirrors what `/search`'s catalog blender does). Surfacing
		// it here lets the directory browse grid render the `+ workflows`
		// chip on the API card *before* the user opens the detail sheet —
		// previously the chip only appeared on search-result cards because
		// only the search payload carried the flag. Workspace rows never
		// set it: their workflows are already imported and shown as
		// first-class cards on `/workspace`.
		hasWorkflows: Boolean(entry.has_workflows),
		specUrl: entry.spec_url ?? undefined,
		registered: entry.source === 'local',
		raw: entry,
	};
}

/** Search result → DiscoveryEntity. Routes the three `r.type` values from
 *  `/search` to the three UI types:
 *
 *    operation              → endpoint
 *    workflow               → workflow
 *    catalog_api            → api (source=directory)
 *
 *  Historical note: the backend used to emit a fourth `catalog_workflow_source`
 *  row type, 1:1 with `catalog_api` for the same `api_id` (workflow_manifest
 *  entries are keyed by vendor directory). Those rows carried no per-workflow
 *  detail and visually duplicated the API tile. They've been collapsed into
 *  a `has_workflows: true` boolean on the corresponding `catalog_api` row,
 *  rendered here as `entity.hasWorkflows` and surfaced as a small chip on
 *  the directory API card.
 */
export function searchResultToEntity(r: any): DiscoveryEntity {
	// P2: highlight plumbing. `match_snippet` is best-effort — older cached
	// responses may not carry it, so we coerce to null so downstream code
	// can treat absence uniformly. The `matched_on` provenance array is
	// dropped on the floor here (May 2026 simplification — the "matched on
	// summary" badge it powered just added noise without action).
	const matchSnippet: string | null = r.match_snippet ?? null;

	if (r.type === 'workflow') {
		return {
			id: r.id,
			type: 'workflow',
			source: serverSourceToUi(r.source),
			summary: r.summary,
			description: r.description,
			score: r.score,
			involvedApis: r.involved_apis ?? [],
			matchSnippet,
			raw: r,
		};
	}
	if (r.type === 'catalog_api') {
		const apiId = r.api_id ?? r.id;
		return {
			id: r.id,
			type: 'api',
			source: 'directory',
			summary: apiId,
			// Synthetic `Available in the Jentic public catalog…` string
			// dropped here too — see `apiToEntity` for the rationale.
			description: r.description ?? undefined,
			hasWorkflows: r.has_workflows === true,
			specUrl: r.spec_url ?? undefined,
			matchSnippet,
			raw: r,
		};
	}
	// Defensive fallback for stale cached responses that may still carry
	// the legacy `catalog_workflow_source` type. Treat them as the API
	// row they shadow so the UI never ends up with a phantom workflow card
	// pointing at no concrete workflow.
	if (r.type === 'catalog_workflow_source') {
		const apiId = r.api_id ?? r.id;
		return {
			id: apiId,
			type: 'api',
			source: 'directory',
			summary: apiId,
			description: undefined,
			hasWorkflows: true,
			matchSnippet,
			raw: r,
		};
	}
	const parsed = parseCapabilityId(r.id ?? '');
	return {
		id: r.id,
		type: 'endpoint',
		source: serverSourceToUi(r.source),
		summary: r.summary,
		description: r.description,
		score: r.score,
		method: parsed?.method,
		apiId: parsed?.host,
		matchSnippet,
		raw: r,
	};
}
