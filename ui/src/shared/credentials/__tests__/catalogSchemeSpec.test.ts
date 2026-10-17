import { afterEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { fetchCatalogSchemeSpec } from '@/shared/credentials/api/apis';
import {
	oauth2FlowsFromSchemes,
	oauth2ScopesFromSchemes,
	specRequiresSecurity,
} from '@/shared/credentials/lib/schemes';

/**
 * The credential picker shapes a not-yet-imported catalog API from
 * `GET /catalog/{entry_id}/operations`. The body below is the server's
 * projection verbatim (OAuth2 flow names in `flows`, their URLs and scopes in
 * `flow_details`, `servers[0].variables`, spec-wide `required_schemes`), so the
 * OAuth URLs, scopes and per-tenant host prompts prefill as they did when the
 * browser fetched the raw spec.
 */
const PREVIEW_URL = '/catalog/public/zendesk.com/operations';
const PREVIEW = {
	data: [
		{
			method: 'GET',
			path: '/tickets',
			summary: '',
			description: '',
			operation_id: null,
			parameters: [],
			security: [],
			tags: [],
		},
	],
	total: 250,
	offset: 0,
	truncated: true,
	info: { title: 'Zendesk', version: '2', description: null },
	security_schemes: {
		OAuth: {
			type: 'oauth2',
			description: '',
			flows: ['authorizationCode'],
			flow_details: {
				authorizationCode: {
					authorizationUrl: 'https://{subdomain}.zendesk.com/oauth/authorizations/new',
					tokenUrl: 'https://{subdomain}.zendesk.com/oauth/tokens',
					scopes: { read: 'Read everything', write: 'Write everything' },
				},
			},
		},
	},
	server_variables: {
		subdomain: { description: 'Your Zendesk subdomain' },
	},
	// Only operation #201 needs OAuth — beyond the first page of operations.
	required_schemes: ['OAuth'],
};

describe('fetchCatalogSchemeSpec', () => {
	afterEach(() => worker.resetHandlers());

	it('rebuilds OAuth flows, scopes, server variables and spec-wide requirements', async () => {
		worker.use(http.get(PREVIEW_URL, () => HttpResponse.json(PREVIEW)));

		const spec = await fetchCatalogSchemeSpec('public/zendesk.com');
		const schemes = (spec.components as { securitySchemes: Record<string, unknown> })
			.securitySchemes as Parameters<typeof oauth2FlowsFromSchemes>[0];

		const [flow] = oauth2FlowsFromSchemes(schemes);
		expect(flow).toMatchObject({
			schemeName: 'OAuth',
			grantType: 'authorization_code',
			authorizationUrl: 'https://{subdomain}.zendesk.com/oauth/authorizations/new',
			tokenUrl: 'https://{subdomain}.zendesk.com/oauth/tokens',
		});
		expect(oauth2ScopesFromSchemes(schemes).map((s) => s.name)).toEqual(['read', 'write']);
		expect(spec.servers).toEqual([
			{ url: '', variables: { subdomain: { description: 'Your Zendesk subdomain' } } },
		]);
		// The one loaded operation declares no security, yet the spec needs OAuth.
		expect(specRequiresSecurity(spec)).toBe(true);
		expect(spec.security).toEqual([{ OAuth: [] }]);
	});

	it('omits servers when the entry declares no server variables', async () => {
		worker.use(
			http.get(PREVIEW_URL, () =>
				HttpResponse.json({ ...PREVIEW, server_variables: {}, required_schemes: [] }),
			),
		);

		const spec = await fetchCatalogSchemeSpec('public/zendesk.com');
		expect(spec.servers).toBeUndefined();
		expect(spec.security).toEqual([]);
	});
});
