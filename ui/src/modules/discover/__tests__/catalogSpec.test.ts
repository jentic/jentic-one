import { describe, it, expect } from 'vitest';
import { parseCatalogSpecUrl, versionLabel } from '@/modules/discover/lib/catalogSpec';

const RAW = 'https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi';

describe('parseCatalogSpecUrl', () => {
	it('reads the version off a domain-only entry (`{domain}/main/{version}`)', () => {
		expect(
			parseCatalogSpecUrl(`${RAW}/stripe.com/main/2024-01-01/openapi.json`, 'stripe.com'),
		).toBe('2024-01-01');
	});

	it('reads the version off a fully qualified ref (`refs/heads/main`), as the live manifest serves', () => {
		const live =
			'https://raw.githubusercontent.com/jentic/jentic-public-apis/refs/heads/main/apis/openapi';
		expect(
			parseCatalogSpecUrl(`${live}/stripe.com/main/2024-01-01/openapi.json`, 'stripe.com'),
		).toBe('2024-01-01');
		expect(
			parseCatalogSpecUrl(
				`${live}/nytimes.com/books/3.0.0/openapi.json`,
				'nytimes.com/books',
			),
		).toBe('3.0.0');
	});

	it('reads the version off an umbrella sub-API (`{domain}/{sub}/{version}`)', () => {
		expect(
			parseCatalogSpecUrl(`${RAW}/nytimes.com/books/3.0.0/openapi.json`, 'nytimes.com/books'),
		).toBe('3.0.0');
	});

	it('treats a version-marker sub (`v2`) as no sub-API, like the backend', () => {
		expect(parseCatalogSpecUrl(`${RAW}/example.com/v2/2.1.0/openapi.yaml`, 'example.com')).toBe(
			'2.1.0',
		);
	});

	it.each([
		['null', null, 'stripe.com'],
		[
			'a different host',
			'https://example.com/stripe.com/main/2024-01-01/openapi.json',
			'stripe.com',
		],
		[
			'a different repo',
			'https://raw.githubusercontent.com/jentic/catalog/main/stripe.com.json',
			'stripe.com',
		],
		['too few directories', `${RAW}/stripe.com/openapi.json`, 'stripe.com'],
		['too many directories', `${RAW}/stripe.com/main/x/2024-01-01/openapi.json`, 'stripe.com'],
		['a non-openapi file', `${RAW}/stripe.com/main/2024-01-01/apis.json`, 'stripe.com'],
		['a non-version directory', `${RAW}/stripe.com/main/latest/openapi.json`, 'stripe.com'],
		['a URL for another entry', `${RAW}/slack.com/main/1.7.0/openapi.json`, 'stripe.com'],
		[
			'a sub-API URL for the bare domain',
			`${RAW}/nytimes.com/books/3.0.0/openapi.json`,
			'nytimes.com',
		],
		[
			'a ref with other path segments',
			'https://raw.githubusercontent.com/jentic/jentic-public-apis/refs/tags/v1/apis/openapi/stripe.com/main/2024-01-01/openapi.json',
			'stripe.com',
		],
		['a query string', `${RAW}/stripe.com/main/2024-01-01/openapi.json?x=1`, 'stripe.com'],
	])('returns null for %s — never guesses', (_label, url, apiId) => {
		expect(parseCatalogSpecUrl(url, apiId)).toBeNull();
	});
});

describe('versionLabel', () => {
	it('prefixes a bare version with "v" and leaves an existing prefix alone', () => {
		expect(versionLabel('2024-01-01')).toBe('v2024-01-01');
		expect(versionLabel('1.0.0')).toBe('v1.0.0');
		expect(versionLabel('v3')).toBe('v3');
	});
});
