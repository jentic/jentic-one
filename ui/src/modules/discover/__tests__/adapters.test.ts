import { describe, it, expect } from 'vitest';
import { catalogEntryToEntity } from '@/modules/discover/api/adapters';
import type { CatalogEntryResponse } from '@/shared/api';

const registeredEntry = {
	api_id: 'stripe.com',
	vendor: 'stripe',
	path: 'apis/stripe.com/openapi.json',
	spec_url: 'https://example.com/stripe.json',
	registered: true,
	_links: {
		self: '/catalog/stripe.com',
		operations: '/catalog/stripe.com/operations',
		import: '/catalog/stripe.com:import',
		github: null,
	},
} as unknown as CatalogEntryResponse;

const availableEntry = {
	api_id: 'slack.com',
	vendor: 'slack',
	path: 'apis/slack.com/openapi.json',
	spec_url: 'https://example.com/slack.json',
	registered: false,
	_links: {
		self: '/catalog/slack.com',
		operations: '/catalog/slack.com/operations',
		import: '/catalog/slack.com:import',
		github: 'https://github.com/jentic/catalog/slack.com.json',
	},
} as unknown as CatalogEntryResponse;

const noVendorEntry = {
	api_id: 'example.org',
	vendor: null,
	path: null,
	spec_url: null,
	registered: false,
	_links: {
		self: '/catalog/example.org',
		operations: '/catalog/example.org/operations',
		import: '/catalog/example.org:import',
		github: null,
	},
} as unknown as CatalogEntryResponse;

const umbrellaSubApi = {
	api_id: 'nytimes.com/article_search',
	vendor: 'nytimes.com',
	path: 'apis/nytimes.com/article_search/openapi.json',
	spec_url: 'https://example.com/nyt-article-search.json',
	registered: false,
	_links: {
		self: '/catalog/nytimes.com/article_search',
		operations: '/catalog/nytimes.com/article_search/operations',
		import: '/catalog/nytimes.com/article_search:import',
		github: null,
	},
} as unknown as CatalogEntryResponse;

describe('catalogEntryToEntity', () => {
	it('maps a registered entry to an imported entity', () => {
		const entity = catalogEntryToEntity(registeredEntry);
		expect(entity.id).toBe('stripe.com');
		expect(entity.apiId).toBe('stripe.com');
		expect(entity.registered).toBe(true);
		expect(entity.summary).toBe('stripe.com');
		expect(entity.subtitle).toBe('stripe');
		expect(entity.githubUrl).toBeUndefined();
	});

	it('maps an unregistered entry with a github link', () => {
		const entity = catalogEntryToEntity(availableEntry);
		expect(entity.registered).toBe(false);
		expect(entity.apiId).toBe('slack.com');
		expect(entity.githubUrl).toContain('slack.com.json');
	});

	it('falls back to api_id when the entry has no vendor', () => {
		const entity = catalogEntryToEntity(noVendorEntry);
		expect(entity.summary).toBe('example.org');
		expect(entity.subtitle).toBeUndefined();
		expect(entity.vendor).toBe('example.org');
	});

	it('gives umbrella sub-APIs a distinct title with the vendor as subtitle', () => {
		const entity = catalogEntryToEntity(umbrellaSubApi);
		// Two sub-APIs of nytimes.com must NOT collide on the same title.
		expect(entity.summary).toBe('Article Search');
		expect(entity.subtitle).toBe('nytimes.com');
		expect(entity.apiId).toBe('nytimes.com/article_search');
	});
});
