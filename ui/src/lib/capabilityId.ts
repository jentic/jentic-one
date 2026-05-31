/**
 * Capability / jentic ID parsing.
 *
 * Format: `METHOD/host/path` (a single slash separator between segments).
 *   "GET/api.stripe.com/v1/customers"
 *   "POST/4cdn.org/boards.json"
 *
 * Workflow capability ids follow `POST/host/workflows/{slug}` — same shape.
 *
 * The shape is the public semantic identifier for an operation across the
 * Jentic surface. Some server responses (notably `GET /apis/{id}/operations`)
 * return ONLY this `id` field to keep payloads token-efficient for agents,
 * leaving the UI to derive `method` and `path` for display. This helper is
 * that derivation — keep it in one place so every consumer gets the same
 * parsing semantics.
 */

export interface ParsedCapabilityId {
	method: string;
	host: string;
	path: string;
}

const METHOD_RE = /^[A-Z]+$/;

/**
 * Parse a capability id into its constituent parts. Returns `null` if `id`
 * doesn't look like a capability id (e.g. raw operationId, UUID, slug).
 */
export function parseCapabilityId(id: string): ParsedCapabilityId | null {
	if (!id) return null;
	const parts = id.split('/');
	if (parts.length < 2 || !METHOD_RE.test(parts[0])) return null;
	return {
		method: parts[0],
		host: parts[1],
		path: '/' + parts.slice(2).join('/'),
	};
}
