import { describe, expect, it } from 'vitest';
import { slugifyApiField } from '@/shared/lib/apiSlug';

describe('slugifyApiField', () => {
	// Must mirror the backend's slugify_api_field exactly: stored rows are
	// slugified on write and server-side filters are exact matches.
	it('lowercases, trims, and collapses runs of non [a-z0-9-] to one hyphen', () => {
		expect(slugifyApiField('  Open-Meteo.com  ')).toBe('open-meteo-com');
		expect(slugifyApiField('httpbin.org')).toBe('httpbin-org');
		expect(slugifyApiField('A__B  C!!d')).toBe('a-b-c-d');
	});

	it('trims leading/trailing hyphens produced by the collapse', () => {
		expect(slugifyApiField('.leading and trailing.')).toBe('leading-and-trailing');
		expect(slugifyApiField('---')).toBe('');
	});

	it('collapses non-ascii like the backend regex does', () => {
		expect(slugifyApiField('météo.fr')).toBe('m-t-o-fr');
	});

	it('truncates to the backend API_FIELD_MAX_LENGTH of 100', () => {
		expect(slugifyApiField(`${'a'.repeat(150)}.com`)).toBe('a'.repeat(100));
	});

	it('is idempotent — an already-canonical value passes through', () => {
		expect(slugifyApiField('httpbin-org')).toBe('httpbin-org');
	});
});
