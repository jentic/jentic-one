/**
 * How a catalog entry relates to things you already have — derived only from
 * data the Library has already loaded (the catalog list, the drained
 * `GET /apis` joined with the drained `GET /credentials`), never per tile.
 *
 * Every catalog ⇄ workspace match keys on `catalog_api_id`, the catalog
 * identity slug (`domain[/sub-api]`) the backend records verbatim on a
 * workspace API at catalog import. Vendor / name / host are deliberately NOT
 * used for that: they're slugged or derived (`stripe` vs `stripe.com`,
 * `api.stripe.com`) and would give false positives across unrelated APIs.
 * Credentials reach an entry only through those workspace APIs.
 */
import { ROUTES } from '@/shared/app/routes';
import type { Credential } from '@/shared/credentials/api';

/**
 * Where "Open" goes for an imported catalog entry: the API's hub when the
 * registry maps the entry to exactly one workspace API, else the Workspace view
 * (none known yet, or several versions to choose from).
 */
export function workspaceHrefFor(matches: ReadonlyArray<{ href: string }> | undefined): string {
	return matches?.length === 1 ? matches[0].href : ROUTES.workspace;
}

/**
 * Credentials that can already reach a catalog entry — the ONE credential ↔
 * API rule the hub, the panel and the workspace tiles use for "Credential
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
