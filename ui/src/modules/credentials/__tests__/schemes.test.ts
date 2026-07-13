import { describe, expect, it } from 'vitest';
import { CredentialType } from '@/modules/credentials/api';
import {
	apiKeyFieldsFromScheme,
	oauth2FlowsFromSchemes,
	oauth2ScopesFromSchemes,
	parseSchemeOptions,
	schemeTypeFromRaw,
	schemeTypeToCredentialType,
} from '@/modules/credentials/lib/schemes';

describe('credentials/lib/schemes', () => {
	describe('schemeTypeFromRaw', () => {
		it('maps oauth2', () => {
			expect(schemeTypeFromRaw({ type: 'oauth2' })).toBe('oauth2');
		});
		it('maps http+bearer to bearer', () => {
			expect(schemeTypeFromRaw({ type: 'http', scheme: 'Bearer' })).toBe('bearer');
		});
		it('maps http+basic to basic', () => {
			expect(schemeTypeFromRaw({ type: 'http', scheme: 'basic' })).toBe('basic');
		});
		it('maps apiKey', () => {
			expect(schemeTypeFromRaw({ type: 'apiKey' })).toBe('apiKey');
		});
		it('collapses anything else to unknown', () => {
			expect(schemeTypeFromRaw({ type: 'mutualTLS' })).toBe('unknown');
			expect(schemeTypeFromRaw({})).toBe('unknown');
		});
	});

	describe('parseSchemeOptions', () => {
		it('returns an empty list for null/empty input', () => {
			expect(parseSchemeOptions(null)).toEqual([]);
			expect(parseSchemeOptions({})).toEqual([]);
		});
		it('dedupes by type and orders by canonical priority', () => {
			const options = parseSchemeOptions({
				oauth: { type: 'oauth2' },
				bearer1: { type: 'http', scheme: 'bearer' },
				bearer2: { type: 'http', scheme: 'bearer' },
				apikey: { type: 'apiKey', in: 'header', name: 'X-Api-Key' },
			});
			expect(options.map((o) => o.type)).toEqual(['bearer', 'apiKey', 'oauth2']);
			// First bearer scheme wins (its raw name is exposed for downstream lookups).
			expect(options.find((o) => o.type === 'bearer')?.name).toBe('bearer1');
		});
	});

	describe('schemeTypeToCredentialType', () => {
		it('maps each known scheme type to its credential type', () => {
			expect(schemeTypeToCredentialType('bearer')).toBe(CredentialType.BEARER_TOKEN);
			expect(schemeTypeToCredentialType('apiKey')).toBe(CredentialType.API_KEY);
			expect(schemeTypeToCredentialType('basic')).toBe(CredentialType.BASIC);
			expect(schemeTypeToCredentialType('oauth2')).toBe(CredentialType.OAUTH2);
		});
		it('returns null for unknown so the UI falls back to manual type', () => {
			expect(schemeTypeToCredentialType('unknown')).toBeNull();
		});
	});

	describe('apiKeyFieldsFromScheme', () => {
		it('reads field_name + location from the picked scheme', () => {
			const schemes = {
				ApiKeyAuth: { type: 'apiKey', in: 'header', name: 'X-Api-Key' },
			};
			expect(apiKeyFieldsFromScheme(schemes, 'ApiKeyAuth')).toEqual({
				fieldName: 'X-Api-Key',
				location: 'header',
			});
		});
		it('treats `in: query` as the query location', () => {
			const schemes = { Q: { type: 'apiKey', in: 'query', name: 'api_key' } };
			expect(apiKeyFieldsFromScheme(schemes, 'Q')).toEqual({
				fieldName: 'api_key',
				location: 'query',
			});
		});
		it('defaults to header + empty name when the scheme is missing', () => {
			expect(apiKeyFieldsFromScheme(null, 'X')).toEqual({
				fieldName: '',
				location: 'header',
			});
			expect(apiKeyFieldsFromScheme({}, undefined)).toEqual({
				fieldName: '',
				location: 'header',
			});
		});
	});

	describe('oauth2ScopesFromSchemes', () => {
		it('flattens and dedupes scopes across flows', () => {
			const schemes = {
				oauth: {
					type: 'oauth2',
					flows: {
						authorizationCode: {
							scopes: { 'read:jira': 'Read Jira', 'write:jira': 'Write Jira' },
						},
						clientCredentials: {
							scopes: { 'read:jira': 'Read Jira (dupe)', offline_access: '' },
						},
					},
				},
			} as never;
			const scopes = oauth2ScopesFromSchemes(schemes);
			expect(scopes).toEqual([
				{ name: 'read:jira', description: 'Read Jira' },
				{ name: 'write:jira', description: 'Write Jira' },
				{ name: 'offline_access', description: undefined },
			]);
		});

		it('returns [] for specs without oauth2 scopes', () => {
			expect(oauth2ScopesFromSchemes(null)).toEqual([]);
			expect(oauth2ScopesFromSchemes({ k: { type: 'apiKey' } })).toEqual([]);
		});
	});

	describe('oauth2FlowsFromSchemes', () => {
		const schemes = {
			oauth: {
				type: 'oauth2',
				flows: {
					authorizationCode: {
						tokenUrl: 'https://a.example/token',
						authorizationUrl: 'https://a.example/authorize',
					},
					clientCredentials: { tokenUrl: 'https://a.example/token' },
				},
			},
		} as never;

		it('maps each flow to a labelled grant type with its URLs', () => {
			const flows = oauth2FlowsFromSchemes(schemes);
			expect(flows).toEqual([
				{
					id: 'oauth.authorizationCode',
					schemeName: 'oauth',
					flowType: 'authorizationCode',
					label: 'Authorization Code',
					grantType: 'authorization_code',
					tokenUrl: 'https://a.example/token',
					authorizationUrl: 'https://a.example/authorize',
				},
				{
					id: 'oauth.clientCredentials',
					schemeName: 'oauth',
					flowType: 'clientCredentials',
					label: 'Client Credentials',
					grantType: 'client_credentials',
					tokenUrl: 'https://a.example/token',
					authorizationUrl: undefined,
				},
			]);
		});

		it('returns [] when there are no flows', () => {
			expect(oauth2FlowsFromSchemes(null)).toEqual([]);
			expect(oauth2FlowsFromSchemes({ k: { type: 'apiKey' } })).toEqual([]);
		});

		it('scopes extraction to a single scheme when schemeName is given', () => {
			const multi = {
				schemeA: {
					type: 'oauth2',
					flows: { authorizationCode: { tokenUrl: 'https://a.example/token' } },
				},
				schemeB: {
					type: 'oauth2',
					flows: { authorizationCode: { tokenUrl: 'https://b.example/token' } },
				},
			} as never;
			// With a name, URLs come from the requested scheme only.
			expect(oauth2FlowsFromSchemes(multi, 'schemeB')).toHaveLength(1);
			expect(oauth2FlowsFromSchemes(multi, 'schemeB')[0].tokenUrl).toBe(
				'https://b.example/token',
			);
		});

		it('returns every (scheme, flow) pair when no schemeName is given', () => {
			const multi = {
				schemeA: {
					type: 'oauth2',
					flows: { authorizationCode: { tokenUrl: 'https://a.example/token' } },
				},
				schemeB: {
					type: 'oauth2',
					flows: { authorizationCode: { tokenUrl: 'https://b.example/token' } },
				},
			} as never;
			const flows = oauth2FlowsFromSchemes(multi);
			// Both schemes' flows surface — no cross-scheme dedupe by flow type.
			expect(flows).toHaveLength(2);
			expect(flows.map((f) => f.id)).toEqual([
				'schemeA.authorizationCode',
				'schemeB.authorizationCode',
			]);
			expect(flows.map((f) => f.tokenUrl)).toEqual([
				'https://a.example/token',
				'https://b.example/token',
			]);
		});

		it('disambiguates labels with the scheme name when a flow type repeats', () => {
			const multi = {
				schemeA: {
					type: 'oauth2',
					flows: {
						authorizationCode: { tokenUrl: 'https://a.example/token' },
						clientCredentials: { tokenUrl: 'https://a.example/cc' },
					},
				},
				schemeB: {
					type: 'oauth2',
					flows: { authorizationCode: { tokenUrl: 'https://b.example/token' } },
				},
			} as never;
			const flows = oauth2FlowsFromSchemes(multi);
			const ac = flows.filter((f) => f.flowType === 'authorizationCode');
			expect(ac.map((f) => f.label)).toEqual([
				'Authorization Code — schemeA',
				'Authorization Code — schemeB',
			]);
			// Singleton flow types keep the clean label.
			const cc = flows.find((f) => f.flowType === 'clientCredentials');
			expect(cc?.label).toBe('Client Credentials');
		});

		it('falls back to all schemes when the named scheme is absent', () => {
			expect(oauth2FlowsFromSchemes(schemes, 'does-not-exist')[0].grantType).toBe(
				'authorization_code',
			);
		});

		it('orders flows canonically regardless of spec order', () => {
			// Spec lists the flows in a deliberately "wrong" order; the picker
			// must surface them as authorization_code → client_credentials →
			// password → implicit so the default (flows[0]) is stable + sensible.
			const outOfOrder = {
				oauth: {
					type: 'oauth2',
					flows: {
						implicit: { authorizationUrl: 'https://x/authorize' },
						password: { tokenUrl: 'https://x/token' },
						clientCredentials: { tokenUrl: 'https://x/token' },
						authorizationCode: {
							tokenUrl: 'https://x/token',
							authorizationUrl: 'https://x/authorize',
						},
					},
				},
			} as never;
			const flows = oauth2FlowsFromSchemes(outOfOrder);
			expect(flows.map((f) => f.grantType)).toEqual([
				'authorization_code',
				'client_credentials',
				'password',
				'implicit',
			]);
		});

		it('sorts unknown flow types last, alphabetically', () => {
			const withUnknown = {
				oauth: {
					type: 'oauth2',
					flows: {
						zebraFlow: { tokenUrl: 'https://x/z' },
						alphaFlow: { tokenUrl: 'https://x/a' },
						authorizationCode: { tokenUrl: 'https://x/token' },
					},
				},
			} as never;
			const flows = oauth2FlowsFromSchemes(withUnknown);
			expect(flows.map((f) => f.flowType)).toEqual([
				'authorizationCode',
				'alphaFlow',
				'zebraFlow',
			]);
		});
	});
});
