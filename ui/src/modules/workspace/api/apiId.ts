/**
 * Composite API identity helpers.
 *
 * jentic-one addresses an API by the `(vendor, name, version)` triple — there
 * is no single opaque `apiId`. The registry routes embed
 * the three as path segments (`/apis/{vendor}/{name}/{version}`), and the UI
 * route mirrors them (`/app/workspace/:vendor/:name/:version`).
 *
 * `encodeApiId` builds the URL path from a triple by percent-encoding each
 * segment and joining with `/`, so a slash *inside* a segment (rare, but legal
 * in vendor names) is encoded and never mistaken for a separator. The detail
 * page reads the three segments straight off `useParams` and `decodeURIComponent`s
 * each — there's no inverse "parse one token" helper because React Router has
 * already split the path for us.
 */

export interface ApiKey {
	vendor: string;
	name: string;
	version: string;
}

/** Encode a triple into the `:vendor/:name/:version` URL path (each segment encoded). */
export function encodeApiId({ vendor, name, version }: ApiKey): string {
	return [vendor, name, version].map(encodeURIComponent).join('/');
}

/** Human-facing `vendor/name/version` label (decoded, slash-joined). */
export function formatApiKey(key: ApiKey): string {
	return `${key.vendor}/${key.name}/${key.version}`;
}
