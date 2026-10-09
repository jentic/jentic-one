import { describeSecurityScheme, summarizeAuth } from '@/modules/discover/components/ApiSummary';

describe('describeSecurityScheme', () => {
	it('names an API key and where it travels', () => {
		expect(
			describeSecurityScheme('key', { type: 'apiKey', in: 'header', name: 'X-API-Key' }),
		).toEqual({ label: 'API key', detail: 'Header · X-API-Key' });
		expect(
			describeSecurityScheme('key', { type: 'apiKey', in: 'query', name: 'api_key' }),
		).toEqual({ label: 'API key', detail: 'Query · api_key' });
		expect(
			describeSecurityScheme('key', { type: 'apiKey', in: 'cookie', name: 'sid' }),
		).toEqual({
			label: 'API key',
			detail: 'Cookie · sid',
		});
	});

	it('omits the detail when an API key says nothing about its placement', () => {
		expect(describeSecurityScheme('key', { type: 'apiKey' })).toEqual({ label: 'API key' });
	});

	it('describes HTTP bearer and basic (any case)', () => {
		expect(describeSecurityScheme('b', { type: 'http', scheme: 'Bearer' })).toEqual({
			label: 'Bearer token',
			detail: 'Header · Authorization: Bearer',
		});
		expect(describeSecurityScheme('b', { type: 'http', scheme: 'basic' })).toEqual({
			label: 'Basic auth',
			detail: 'Header · Authorization: Basic',
		});
		// Swagger 2 spells basic auth as its own type.
		expect(describeSecurityScheme('b', { type: 'basic' }).label).toBe('Basic auth');
	});

	it('falls back to the scheme word for other HTTP schemes', () => {
		expect(describeSecurityScheme('d', { type: 'http', scheme: 'digest' })).toEqual({
			label: 'HTTP Digest',
			detail: 'Header · Authorization: Digest',
		});
	});

	it('describes OAuth 2.0, OpenID Connect and mutual TLS', () => {
		expect(describeSecurityScheme('o', { type: 'oauth2', flows: {} })).toEqual({
			label: 'OAuth 2.0',
			detail: 'Header · Authorization: Bearer',
		});
		expect(describeSecurityScheme('o', { type: 'openIdConnect' }).label).toBe('OpenID Connect');
		expect(describeSecurityScheme('m', { type: 'mutualTLS' })).toEqual({ label: 'Mutual TLS' });
	});

	it('uses the scheme name when the type is unknown or missing', () => {
		expect(describeSecurityScheme('custom_sig', { type: 'weird' })).toEqual({
			label: 'custom_sig',
		});
		expect(describeSecurityScheme('custom_sig', undefined)).toEqual({ label: 'custom_sig' });
	});
});

describe('summarizeAuth', () => {
	it('returns nothing for an absent or empty scheme map', () => {
		expect(summarizeAuth(undefined)).toEqual([]);
		expect(summarizeAuth(null)).toEqual([]);
		expect(summarizeAuth({})).toEqual([]);
	});

	it('keeps declaration order and collapses identical lines', () => {
		expect(
			summarizeAuth({
				bearer: { type: 'http', scheme: 'bearer' },
				oauth: { type: 'oauth2' },
				bearer2: { type: 'http', scheme: 'bearer' },
			}),
		).toEqual([
			{ label: 'Bearer token', detail: 'Header · Authorization: Bearer' },
			{ label: 'OAuth 2.0', detail: 'Header · Authorization: Bearer' },
		]);
	});
});
