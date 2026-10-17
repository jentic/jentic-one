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

/**
 * Raw `GET /catalog` manifest entry → `DiscoveryEntity`.
 *
 * `registered` comes straight from the entry, so the UI just reads the boolean
 * (no host/vendor matching). Routes address the entry by `entry_id`; the title
 * and workspace matching read `catalog_api_id`.
 *
 * `summary` is the per-entry title (distinct even within one umbrella vendor);
 * `vendor` is the shared domain shown as a secondary line so two sub-APIs of the
 * same vendor are still tellable apart at a glance.
 */
export function catalogEntryToEntity(entry: CatalogEntryResponse): DiscoveryEntity {
	const vendor = entry.vendor ?? undefined;
	const summary = titleFromApiId(entry.catalog_api_id);
	return {
		id: entry.entry_id,
		entryId: entry.entry_id,
		apiId: entry.catalog_api_id,
		summary,
		// Only surface the vendor as a subtitle when it adds information beyond
		// the title (i.e. an umbrella sub-API), to avoid `stripe.com / stripe.com`.
		subtitle: vendor && vendor !== summary ? vendor : undefined,
		registered: entry.registered,
		updateAvailable: entry.update_available ?? false,
		vendor: vendor ?? entry.catalog_api_id,
		catalogVendor: vendor,
		version: entry.version ?? undefined,
		githubUrl: entry.human_url ?? undefined,
	};
}
