/**
 * Composite API identity helpers.
 *
 * jentic-one addresses an API by the `(vendor, name, version)` triple — there
 * is no single opaque `apiId`. The registry routes embed
 * the three as path segments (`/apis/{vendor}/{name}/{version}`), and the UI
 * route mirrors them (`/app/library/workspace/:vendor/:name/:version`).
 *
 * Hub links are built by `ROUTE_PATHS.workspaceApiHub` (`@/shared/app`), which
 * percent-encodes each segment so a slash *inside* one (rare, but legal in
 * vendor names) is never mistaken for a separator. The detail page reads the
 * three segments straight off `useParams` and `decodeURIComponent`s each.
 */

export interface ApiKey {
	vendor: string;
	name: string;
	version: string;
}

/** Human-facing `vendor/name/version` label (decoded, slash-joined). */
export function formatApiKey(key: ApiKey): string {
	return `${key.vendor}/${key.name}/${key.version}`;
}
