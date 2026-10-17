import { describe, it, expect } from 'vitest';
import { catalogEntryToEntity } from '@/modules/discover/api/adapters';
import type { CatalogEntryResponse } from '@/shared/api';

function catalogEntry(
	catalogApiId: string,
	over: Partial<CatalogEntryResponse> = {},
): CatalogEntryResponse {
	const entryId = `public/${catalogApiId}`;
	return {
		entry_id: entryId,
		source_slug: 'public',
		catalog_api_id: catalogApiId,
		vendor: null,
		has_spec: true,
		registered: false,
		_links: {
			self: `/catalog/${entryId}`,
			operations: `/catalog/${entryId}/operations`,
			import: `/catalog/${entryId}:import`,
		},
		...over,
	};
}

const registeredEntry = catalogEntry('stripe.com', { vendor: 'stripe', registered: true });

const availableEntry = catalogEntry('slack.com', {
	vendor: 'slack',
	human_url: 'https://github.com/jentic/catalog/slack.com.json',
});

const noVendorEntry = catalogEntry('example.org');

const umbrellaSubApi = catalogEntry('nytimes.com/article_search', { vendor: 'nytimes.com' });

const outdatedEntry = catalogEntry('twilio.com', {
	vendor: 'twilio',
	registered: true,
	update_available: true,
});

describe('catalogEntryToEntity', () => {
	it('maps a registered entry to an imported entity', () => {
		const entity = catalogEntryToEntity(registeredEntry);
		expect(entity.apiId).toBe('stripe.com');
		expect(entity.registered).toBe(true);
		expect(entity.summary).toBe('stripe.com');
		expect(entity.subtitle).toBe('stripe');
		expect(entity.humanUrl).toBeUndefined();
	});

	it('addresses the entry by entry_id and names it by catalog_api_id', () => {
		const entity = catalogEntryToEntity(registeredEntry);
		expect(entity.id).toBe('public/stripe.com');
		expect(entity.entryId).toBe('public/stripe.com');
		expect(entity.apiId).toBe('stripe.com');
	});

	it('carries the entry version, and omits it when the entry has none', () => {
		expect(catalogEntryToEntity({ ...registeredEntry, version: '2024-01-01' }).version).toBe(
			'2024-01-01',
		);
		expect(catalogEntryToEntity(registeredEntry).version).toBeUndefined();
	});

	it('defaults updateAvailable to false when the entry omits update_available', () => {
		// The three baseline fixtures carry no `update_available` field, so the
		// adapter must default it defensively rather than surface `undefined`.
		expect(catalogEntryToEntity(registeredEntry).updateAvailable).toBe(false);
		expect(catalogEntryToEntity(availableEntry).updateAvailable).toBe(false);
	});

	it('maps update_available: true → updateAvailable on an outdated imported entry', () => {
		const entity = catalogEntryToEntity(outdatedEntry);
		expect(entity.registered).toBe(true);
		expect(entity.updateAvailable).toBe(true);
	});

	it('maps an unregistered entry with a human link', () => {
		const entity = catalogEntryToEntity(availableEntry);
		expect(entity.registered).toBe(false);
		expect(entity.apiId).toBe('slack.com');
		expect(entity.humanUrl).toContain('slack.com.json');
	});

	it('falls back to catalog_api_id when the entry has no vendor', () => {
		const entity = catalogEntryToEntity(noVendorEntry);
		expect(entity.summary).toBe('example.org');
		expect(entity.subtitle).toBeUndefined();
		expect(entity.vendor).toBe('example.org');
		// The icon key falls back; the import identity's vendor does not.
		expect(entity.catalogVendor).toBeUndefined();
	});

	it('keeps the catalog vendor verbatim for the import identity', () => {
		expect(catalogEntryToEntity(umbrellaSubApi).catalogVendor).toBe('nytimes.com');
	});

	it('gives umbrella sub-APIs a distinct title with the vendor as subtitle', () => {
		const entity = catalogEntryToEntity(umbrellaSubApi);
		// Two sub-APIs of nytimes.com must NOT collide on the same title.
		expect(entity.summary).toBe('Article Search');
		expect(entity.subtitle).toBe('nytimes.com');
		expect(entity.apiId).toBe('nytimes.com/article_search');
	});
});
