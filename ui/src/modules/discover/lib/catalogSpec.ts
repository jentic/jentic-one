/**
 * Display helpers for what the catalog list payload carries about an entry
 * (`CatalogEntryResponse.version`, `human_url`, `source_slug`, `alternates`).
 */

/** Display form of a version: `v2024-01-01`, `v1.0.0` — never `vv3`. */
export function versionLabel(version: string): string {
	return /^v/i.test(version) ? version : `v${version}`;
}

/**
 * Whether an entry's `human_url` is a GitHub page (the public catalog's
 * entries link to their folder in the API directory), so the link can wear
 * the GitHub mark; any other source's page gets a generic external link.
 */
export function isGitHubUrl(url: string): boolean {
	try {
		const host = new URL(url).hostname.toLowerCase();
		return host === 'github.com' || host.endsWith('.github.com');
	} catch {
		return false;
	}
}

/**
 * Whether the loaded entries come from more than one catalog source. Source
 * badges and tags show only then (with one source every row would carry the
 * same word), and the A–Z rail hides: the browse feed is in `entry_id` order,
 * source by source, so a letter has no single place to jump to.
 */
export function spansSources(entities: Iterable<{ sourceSlug: string }>): boolean {
	let first: string | undefined;
	for (const e of entities) {
		if (first === undefined) first = e.sourceSlug;
		else if (e.sourceSlug !== first) return true;
	}
	return false;
}

/** "+1 other source" / "+3 other sources" — the entries collapsed into this one. */
export function alternatesLabel(alternates: number): string {
	return `+${alternates.toLocaleString()} other source${alternates === 1 ? '' : 's'}`;
}

/**
 * Tooltip for the alternates note: the collapse picks one entry per API (the
 * one in your workspace, else the highest-precedence source), and the others
 * stay importable by their own `entry_id`.
 */
export function alternatesHint(alternates: number): string {
	return `${alternates === 1 ? 'Another catalog source lists' : `${alternates.toLocaleString()} other catalog sources list`} this API too; this entry is the one shown.`;
}
