/**
 * Discover adapters — server payload → UI `DiscoveryEntity`.
 *
 * Kept separate from `client.ts` so the mapping is unit-testable in isolation
 * and the repository stays a thin HTTP wrapper. The catalog is the only feed
 * Discover renders, so there is a single adapter: a `GET /catalog` entry →
 * `DiscoveryEntity`.
 */
import type { CatalogEntryResponse } from '@/shared/api';
import type { DiscoveryEntity } from '@/modules/discover/api/types';

/**
 * Title-case a slug-ish segment for display: `article_search` → `Article Search`,
 * `top-stories` → `Top Stories`, `v2` → `V2`.
 */
function humanizeSegment(segment: string): string {
	return segment
		.split(/[_\-.]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

/**
 * Derive a distinct, human-readable title from an `api_id`.
 *
 * An umbrella vendor exposes several sub-APIs that all share one `vendor`
 * (e.g. `nytimes.com/article_search`, `nytimes.com/books` both vendor
 * `nytimes.com`). Titling cards by `vendor` makes those rows indistinguishable,
 * so the title is built from the part of the `api_id` that actually varies:
 *
 *   `nytimes.com/article_search` → `Article Search`  (sub-API segment)
 *   `stripe.com`                 → `stripe.com`       (no sub-API; use as-is)
 */
export function titleFromApiId(apiId: string): string {
	const slash = apiId.indexOf('/');
	if (slash === -1) {
		return apiId;
	}
	const sub = apiId.slice(slash + 1);
	return sub ? humanizeSegment(sub) : apiId;
}

/**
 * Raw `GET /catalog` manifest entry → `DiscoveryEntity`.
 *
 * `registered` comes straight from the entry — the backend computes it by exact
 * spec_url match, so the UI just reads the boolean (no host/vendor matching).
 *
 * `summary` is the per-entry title (distinct even within one umbrella vendor);
 * `vendor` is the shared domain shown as a secondary line so two sub-APIs of the
 * same vendor are still tellable apart at a glance.
 */
export function catalogEntryToEntity(entry: CatalogEntryResponse): DiscoveryEntity {
	const vendor = entry.vendor ?? undefined;
	const summary = titleFromApiId(entry.api_id);
	return {
		id: entry.api_id,
		apiId: entry.api_id,
		summary,
		// Only surface the vendor as a subtitle when it adds information beyond
		// the title (i.e. an umbrella sub-API), to avoid `stripe.com / stripe.com`.
		subtitle: vendor && vendor !== summary ? vendor : undefined,
		registered: entry.registered,
		vendor: vendor ?? entry.api_id,
		githubUrl: entry._links.github ?? undefined,
		raw: entry,
	};
}
