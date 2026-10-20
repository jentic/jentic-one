/**
 * Discover adapters — server payload → UI `DiscoveryEntity`.
 *
 * Kept separate from `client.ts` so the mapping is unit-testable in isolation
 * and the repository stays a thin HTTP wrapper. The catalog is the only feed
 * Discover renders, so there is a single adapter: a `GET /catalog` entry →
 * `DiscoveryEntity`.
 */
import type { CatalogEntryResponse } from '@/shared/api';
import { titleFromApiId } from '@/shared/lib';
import type { DiscoveryEntity } from '@/modules/discover/api/types';

/** The domain part of a `catalog_api_id` (`googleapis.com/gmail` → `googleapis.com`). */
function domainOf(catalogApiId: string): string {
	const slash = catalogApiId.indexOf('/');
	return slash === -1 ? catalogApiId : catalogApiId.slice(0, slash);
}

/**
 * Raw `GET /catalog` entry → `DiscoveryEntity`.
 *
 * `registered` comes straight from the entry, so the UI just reads the boolean
 * (no host/vendor matching). Routes address the entry by `entry_id`; workspace
 * matching reads `catalog_api_id`.
 *
 * `summary` is the per-entry title (distinct even within one umbrella vendor);
 * the display vendor is the shared domain shown as a secondary line so two
 * sub-APIs of the same vendor are still tellable apart at a glance. It is also
 * what the ledger groups by and what picks the vendor icon — the identity
 * `vendor` of a namespaced entry (`aws-prod~stripe.com`) is not a domain.
 */
export function catalogEntryToEntity(entry: CatalogEntryResponse): DiscoveryEntity {
	const vendor = entry.vendor ?? undefined;
	const displayVendor = entry.display_vendor || vendor || domainOf(entry.catalog_api_id);
	const summary = entry.title?.trim() || titleFromApiId(entry.catalog_api_id);
	const prefix = `${entry.source_slug}/`;
	return {
		id: entry.entry_id,
		entryId: entry.entry_id,
		apiId: entry.catalog_api_id,
		sourceSlug: entry.source_slug,
		browseKey: entry.entry_id.startsWith(prefix)
			? entry.entry_id.slice(prefix.length)
			: entry.entry_id,
		summary,
		// Only surface the vendor as a subtitle when it adds information beyond
		// the title (i.e. an umbrella sub-API), to avoid `stripe.com / stripe.com`.
		subtitle: displayVendor !== summary ? displayVendor : undefined,
		displayVendor,
		alternates: entry.alternates ?? 0,
		hasSpec: entry.has_spec ?? true,
		registered: entry.registered,
		updateAvailable: entry.update_available ?? false,
		vendor: displayVendor,
		catalogVendor: vendor,
		version: entry.version ?? undefined,
		humanUrl: entry.human_url ?? undefined,
	};
}
