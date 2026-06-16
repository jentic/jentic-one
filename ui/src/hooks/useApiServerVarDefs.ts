import type { ApiOut } from '@/api/types';

/**
 * One server variable, normalised across local and catalog sources.
 *
 * `default` is whatever the spec declared (often a sensible value like
 * `"api"` or `"v1"` — sometimes a placeholder like `"<your-host>"`).
 * `required` is derived rather than declared — see the catalog branch
 * below for the heuristic.
 */
export interface ServerVarDef {
	name: string;
	default?: string | null;
	description?: string | null;
	enum?: string[] | null;
	required: boolean;
}

/**
 * Extract the server variable definitions a credential form needs to
 * render. Two sources, mirroring `useApiSchemes`:
 *
 *  - **local API** — the backend materialises `server_variables` on
 *    the import path (see `src/routers/apis.py`), so the data is
 *    already shaped: `{ default, description, enum, required }`. We
 *    pass through.
 *  - **catalog API** — we have to parse the OpenAPI 3 `servers[0]
 *    .variables` block ourselves. OpenAPI doesn't have a `required`
 *    field for server variables, so we treat any variable WITHOUT a
 *    default as required. That's a known heuristic — variables with
 *    placeholder defaults like `"<your-tenant>"` will look optional
 *    because of it, but the broker rejects requests with placeholders
 *    so the user gets immediate feedback at first use.
 *
 * Returns an empty array when nothing is selected — keeps callers
 * safe to render unconditionally.
 */
export function useApiServerVarDefs(
	_selectedApi: ApiOut | null,
	localDetail: ApiOut | null,
	spec: any,
): ServerVarDef[] {
	if (localDetail && (localDetail as any).server_variables) {
		const raw = (localDetail as any).server_variables as Record<string, any>;
		return Object.entries(raw).map(([name, def]) => ({
			name,
			default: def?.default ?? null,
			description: def?.description ?? null,
			enum: def?.enum ?? null,
			required: def?.required ?? false,
		}));
	}
	if (spec?.servers) {
		const server = spec.servers[0];
		if (server?.variables) {
			return Object.entries(server.variables as Record<string, any>).map(([name, def]) => ({
				name,
				default: def?.default ?? null,
				description: def?.description ?? null,
				enum: def?.enum ?? null,
				required: !def?.default,
			}));
		}
	}
	return [];
}
