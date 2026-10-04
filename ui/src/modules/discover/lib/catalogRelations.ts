/**
 * How a catalog entry relates to things you already have — derived only from
 * data the Library has already loaded (the catalog list, the drained
 * `GET /apis` joined with the drained `GET /credentials`), never per row.
 *
 * Every catalog ⇄ workspace match keys on `catalog_api_id`, the catalog
 * identity slug (`domain[/sub-api]`) the backend records verbatim on a
 * workspace API at catalog import. Vendor / name / host are deliberately NOT
 * used for that: they're slugged or derived (`stripe` vs `stripe.com`,
 * `api.stripe.com`) and would give false positives across unrelated APIs.
 *
 * Credentials are different: the broker matches them on the API's
 * vendor/name/version scope, so an entry NOT yet in the workspace is "ready"
 * when a credential's scope would cover the identity its import registers
 * (`catalogImportRef`, {@link credentialsCoveringEntry}).
 */
import { ROUTES } from '@/shared/app/routes';
import type { Credential } from '@/shared/credentials/api';
import { apiScopeCovers, catalogImportRef } from '@/shared/credentials/lib/apiIdentity';

/**
 * Where "Open" goes for an imported catalog entry: the API's hub when the
 * registry maps the entry to exactly one workspace API; with several versions,
 * the Library with the workspace panel's list filtered to that catalog entry
 * (`?q=<catalog id>`); with none known yet, the Library itself (the panel
 * lists every workspace API).
 */
export function workspaceHrefFor(
	matches: ReadonlyArray<{ href: string; catalogApiId: string | null }> | undefined,
): string {
	if (matches?.length === 1) return matches[0].href;
	const catalogApiId = matches?.find((m) => m.catalogApiId)?.catalogApiId;
	return catalogApiId
		? `${ROUTES.library}?${new URLSearchParams({ q: catalogApiId })}`
		: ROUTES.library;
}

/**
 * Credentials that can already reach a catalog entry — the ONE credential ↔
 * API rule the hub, the panel and the catalog rows use for "Credential
 * missing": an ACTIVE credential whose `api` scope covers (`apiScopeCovers`) a
 * workspace API imported from this entry. The rows carry that per-API answer
 * (`credentials`, null while the credential list is still draining), so this
 * only unions it. No workspace row for the entry ⇒ nothing to match against.
 */
export function readyCredentialsFor(
	rows: ReadonlyArray<{ credentials: Credential[] | null }> | undefined,
): Credential[] | null {
	if (!rows?.length) return [];
	const byId = new Map<string, Credential>();
	for (const row of rows) {
		if (row.credentials == null) return null;
		for (const c of row.credentials) byId.set(c.credential_id, c);
	}
	return [...byId.values()];
}

/**
 * Active credentials that would cover a catalog entry once imported — the
 * "Credential ready" rule for an entry not in the workspace:
 *
 *   - the credential's scope covers the entry's import identity
 *     (`catalogImportRef`) under the broker's own rule
 *     (`apiScopeCovers`, the mirror of `credential_covers`): same vendor, and a
 *     wildcard or equal name. A vendor-wide credential (`googleapis-com`, any
 *     name) therefore covers every API of that vendor — that IS what the broker
 *     will inject. Because the version is unknown before import, a
 *     version-pinned scope is not counted this way;
 *   - or it was created for exactly this entry (`catalog_api_id` equal to the
 *     entry's `api_id`).
 *
 * Null while the credential list is still loading (never "none" early).
 */
export function credentialsCoveringEntry(
	entry: { apiId: string; catalogVendor?: string | null },
	credentials: readonly Credential[] | null | undefined,
): Credential[] | null {
	if (credentials == null) return null;
	const ref = catalogImportRef({ apiId: entry.apiId, vendor: entry.catalogVendor });
	const apiId = entry.apiId.trim().toLowerCase();
	return credentials.filter(
		(c) =>
			c.active &&
			(c.catalog_api_id?.trim().toLowerCase() === apiId ||
				(ref != null && apiScopeCovers(c.api, { ...ref, version: null }))),
	);
}

/**
 * Everything that makes a not-yet-imported entry's credential ready: the
 * entry-level rule ({@link credentialsCoveringEntry}) plus the credentials of
 * any workspace API already imported from it (an older version, say —
 * {@link readyCredentialsFor}). Null until both answers are in.
 */
export function readyCredentialsForEntry(
	entry: { apiId: string; catalogVendor?: string | null },
	rows: ReadonlyArray<{ credentials: Credential[] | null }> | undefined,
	credentials: readonly Credential[] | null | undefined,
): Credential[] | null {
	const direct = credentialsCoveringEntry(entry, credentials);
	const viaRows = readyCredentialsFor(rows);
	if (direct == null || viaRows == null) return null;
	const byId = new Map<string, Credential>();
	for (const c of [...direct, ...viaRows]) byId.set(c.credential_id, c);
	return [...byId.values()];
}
