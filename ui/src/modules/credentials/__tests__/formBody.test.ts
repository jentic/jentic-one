import { describe, expect, it } from 'vitest';
import { CredentialType } from '@/modules/credentials/api';
import { EMPTY_FORM } from '@/modules/credentials/components/CredentialTypeFields';
import {
	buildCreateBody,
	buildRuntimeConfig,
	buildServerVariables,
	buildUpdateBody,
	isValidHttpUrl,
	seedApiKeyFromScheme,
	seedOAuth2FromScheme,
	seedServerVars,
	validateCreate,
	validateServerVars,
} from '@/modules/credentials/lib/formBody';

describe('buildCreateBody', () => {
	it('builds a bearer_token body', () => {
		const body = buildCreateBody(CredentialType.BEARER_TOKEN, {
			...EMPTY_FORM,
			name: 'My token',
			apiVendor: 'acme',
			token: 'sk-123',
		});
		expect(body).toMatchObject({ type: 'bearer_token', name: 'My token', token: 'sk-123' });
		expect(body.api).toEqual({ vendor: 'acme', name: undefined, version: undefined });
	});

	it('builds an api_key body with the location enum', () => {
		const body = buildCreateBody(CredentialType.API_KEY, {
			...EMPTY_FORM,
			name: 'Key',
			apiVendor: 'acme',
			key: 'abc',
			fieldName: 'X-Api-Key',
			location: 'query',
		});
		expect(body).toMatchObject({
			type: 'api_key',
			key: 'abc',
			field_name: 'X-Api-Key',
			location: 'query',
		});
	});

	it('parses space-separated oauth2 scopes and omits empties', () => {
		const body = buildCreateBody(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'OAuth',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			tokenUrl: 'https://x/token',
			scopes: '  read   write ',
		});
		expect(body).toMatchObject({
			type: 'oauth2',
			client_id: 'cid',
			client_secret: 'secret',
			token_url: 'https://x/token',
			scopes: ['read', 'write'],
		});
	});

	it('defaults provider to "static"', () => {
		const body = buildCreateBody(CredentialType.BEARER_TOKEN, {
			...EMPTY_FORM,
			name: 'T',
			apiVendor: 'acme',
			token: 'x',
		});
		expect(body.provider).toBe('static');
	});

	it('forwards a managed (pipedream) provider on oauth2 create', () => {
		const body = buildCreateBody(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'Slack',
			apiVendor: 'slack.com',
			provider: 'pipedream',
			clientId: 'cid',
			clientSecret: 'secret',
			tokenUrl: 'https://slack.com/oauth/token',
		});
		expect(body.provider).toBe('pipedream');
	});

	it('forwards the grant_type when set, and omits it when blank', () => {
		const withGrant = buildCreateBody(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'OAuth',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			tokenUrl: 'https://x/token',
			grantType: 'authorization_code',
		});
		expect((withGrant as Record<string, unknown>).grant_type).toBe('authorization_code');

		const withoutGrant = buildCreateBody(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'OAuth',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			tokenUrl: 'https://x/token',
			grantType: '   ',
		});
		expect((withoutGrant as Record<string, unknown>).grant_type).toBeUndefined();
	});
});

describe('isValidHttpUrl', () => {
	it('accepts absolute http(s) URLs', () => {
		expect(isValidHttpUrl('https://provider.com/oauth/token')).toBe(true);
		expect(isValidHttpUrl('http://provider.com/token')).toBe(true);
		expect(isValidHttpUrl('  https://provider.com/token  ')).toBe(true);
	});

	it('rejects blanks, bare words, schemeless and non-http URLs', () => {
		expect(isValidHttpUrl('')).toBe(false);
		expect(isValidHttpUrl('   ')).toBe(false);
		expect(isValidHttpUrl('not-a-url')).toBe(false);
		expect(isValidHttpUrl('provider.com/token')).toBe(false);
		expect(isValidHttpUrl('http://')).toBe(false);
		expect(isValidHttpUrl('ftp://provider.com/token')).toBe(false);
		expect(isValidHttpUrl('javascript:alert(1)')).toBe(false);
	});
});

describe('buildUpdateBody', () => {
	it('omits blank secret fields so the value is preserved', () => {
		const body = buildUpdateBody(
			CredentialType.BEARER_TOKEN,
			{ ...EMPTY_FORM, name: 'Same', token: '' },
			'Same',
		);
		// Name unchanged → not sent; blank token → not sent.
		expect(body).toEqual({ type: 'bearer_token', token: undefined });
	});

	it('sends a changed name and a rotated secret', () => {
		const body = buildUpdateBody(
			CredentialType.BASIC,
			{ ...EMPTY_FORM, name: 'New name', username: 'u', password: 'p' },
			'Old name',
		);
		expect(body).toMatchObject({
			type: 'basic',
			name: 'New name',
			username: 'u',
			password: 'p',
		});
	});
});

describe('validateCreate', () => {
	it('flags missing required fields per type', () => {
		const errors = validateCreate(CredentialType.OAUTH2, { ...EMPTY_FORM });
		expect(errors.name).toBeTruthy();
		expect(errors.apiVendor).toBeTruthy();
		expect(errors.clientId).toBeTruthy();
		expect(errors.clientSecret).toBeTruthy();
		expect(errors.tokenUrl).toBeTruthy();
	});

	it('passes a fully-specified api_key form', () => {
		const errors = validateCreate(CredentialType.API_KEY, {
			...EMPTY_FORM,
			name: 'Key',
			apiVendor: 'acme',
			key: 'abc',
			fieldName: 'X-Api-Key',
		});
		expect(errors).toEqual({});
	});

	it('requires a token URL for the authorization_code grant', () => {
		const errors = validateCreate(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'O',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			grantType: 'authorization_code',
			tokenUrl: '',
		});
		expect(errors.tokenUrl).toBeTruthy();
	});

	it('does NOT require a token URL for the implicit grant (no token endpoint)', () => {
		const errors = validateCreate(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'O',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			grantType: 'implicit',
			tokenUrl: '',
		});
		expect(errors.tokenUrl).toBeUndefined();
	});

	it('rejects a malformed token URL before it can 400 the backend', () => {
		const errors = validateCreate(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'O',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			grantType: 'client_credentials',
			tokenUrl: 'not-a-url',
		});
		expect(errors.tokenUrl).toMatch(/valid http/i);
	});

	it('requires an authorize URL for the authorization_code grant', () => {
		const errors = validateCreate(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'O',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			grantType: 'authorization_code',
			tokenUrl: 'https://x/token',
			authorizeUrl: '',
		});
		expect(errors.authorizeUrl).toBeTruthy();
	});

	it('rejects a malformed authorize URL when one is provided', () => {
		const errors = validateCreate(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'O',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			grantType: 'authorization_code',
			tokenUrl: 'https://x/token',
			authorizeUrl: 'http://',
		});
		expect(errors.authorizeUrl).toMatch(/valid http/i);
	});

	it('passes a fully-specified authorization_code oauth2 form', () => {
		const errors = validateCreate(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'O',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			grantType: 'authorization_code',
			tokenUrl: 'https://x/token',
			authorizeUrl: 'https://x/authorize',
		});
		expect(errors).toEqual({});
	});

	it('does not require an authorize URL for client_credentials', () => {
		const errors = validateCreate(CredentialType.OAUTH2, {
			...EMPTY_FORM,
			name: 'O',
			apiVendor: 'acme',
			clientId: 'cid',
			clientSecret: 'secret',
			grantType: 'client_credentials',
			tokenUrl: 'https://x/token',
			authorizeUrl: '',
		});
		expect(errors.authorizeUrl).toBeUndefined();
	});
});

describe('seedOAuth2FromScheme', () => {
	const schemes = {
		oauth: {
			type: 'oauth2',
			flows: {
				authorizationCode: {
					tokenUrl: 'https://spec/token',
					authorizationUrl: 'https://spec/authorize',
				},
			},
		},
	} as never;

	it('seeds token/authorize URLs + grant type from the spec when blank', () => {
		const { state, flows, activeFlowId } = seedOAuth2FromScheme({ ...EMPTY_FORM }, schemes);
		expect(state.tokenUrl).toBe('https://spec/token');
		expect(state.authorizeUrl).toBe('https://spec/authorize');
		expect(state.grantType).toBe('authorization_code');
		expect(flows).toHaveLength(1);
		expect(activeFlowId).toBe('oauth.authorizationCode');
	});

	it('never clobbers values the user already typed', () => {
		const { state } = seedOAuth2FromScheme(
			{ ...EMPTY_FORM, tokenUrl: 'https://mine/token', grantType: 'client_credentials' },
			schemes,
		);
		expect(state.tokenUrl).toBe('https://mine/token');
		expect(state.grantType).toBe('client_credentials');
		// Authorize URL was blank, so it still gets seeded.
		expect(state.authorizeUrl).toBe('https://spec/authorize');
	});

	it('returns the state untouched when the spec declares no flows', () => {
		const { state, flows, activeFlowId } = seedOAuth2FromScheme(
			{ ...EMPTY_FORM },
			{ k: { type: 'apiKey' } },
		);
		expect(state.tokenUrl).toBe('');
		expect(flows).toEqual([]);
		expect(activeFlowId).toBeNull();
	});

	it('seeds from the requested flow id when multiple (scheme, flow) pairs exist', () => {
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
		const { state, activeFlowId } = seedOAuth2FromScheme(
			{ ...EMPTY_FORM },
			multi,
			null,
			'schemeB.authorizationCode',
		);
		expect(state.tokenUrl).toBe('https://b.example/token');
		expect(activeFlowId).toBe('schemeB.authorizationCode');
	});
});

describe('seedServerVars', () => {
	it('seeds defaults without clobbering user input', () => {
		const seeded = seedServerVars({ region: 'eu' }, [
			{ name: 'region', default: 'us', required: false },
			{ name: 'domain', default: 'acme', required: true },
			{ name: 'noDefault', required: true },
		]);
		expect(seeded).toEqual({ region: 'eu', domain: 'acme' });
	});
});

describe('validateServerVars', () => {
	it('flags required vars that are empty and ignores optional ones', () => {
		const errors = validateServerVars(
			[
				{ name: 'domain', required: true },
				{ name: 'region', required: false },
			],
			{ region: '' },
		);
		expect(errors.domain).toBeTruthy();
		expect(errors.region).toBeUndefined();
	});

	it('passes when required vars are filled', () => {
		const errors = validateServerVars([{ name: 'domain', required: true }], {
			domain: 'acme',
		});
		expect(errors).toEqual({});
	});
});

describe('buildRuntimeConfig', () => {
	it('returns undefined (server vars use dedicated server_variables field)', () => {
		expect(buildRuntimeConfig({ 'your-domain': 'acme' })).toBeUndefined();
	});

	it('omits runtime_config from the create body', () => {
		const body = buildCreateBody(CredentialType.BEARER_TOKEN, {
			...EMPTY_FORM,
			name: 'T',
			apiVendor: 'acme',
			token: 'x',
			serverVars: { 'your-domain': 'acme' },
		});
		expect('runtime_config' in body).toBe(false);
	});
});

describe('buildServerVariables', () => {
	it('returns undefined when all values are empty', () => {
		expect(buildServerVariables({ region: '', domain: '  ' })).toBeUndefined();
	});

	it('returns a dict with non-empty values', () => {
		expect(buildServerVariables({ region: 'us', domain: 'acme' })).toEqual({
			region: 'us',
			domain: 'acme',
		});
	});

	it('filters out empty entries from a mixed set', () => {
		expect(buildServerVariables({ region: 'us', domain: '' })).toEqual({ region: 'us' });
	});
});

describe('server_variables in create/update body', () => {
	it('includes server_variables in the create body when serverVars is non-empty', () => {
		const body = buildCreateBody(CredentialType.BEARER_TOKEN, {
			...EMPTY_FORM,
			name: 'T',
			apiVendor: 'acme',
			token: 'x',
			serverVars: { 'your-domain': 'acme', region: 'us' },
		});
		expect((body as Record<string, unknown>).server_variables).toEqual({
			'your-domain': 'acme',
			region: 'us',
		});
	});

	it('omits server_variables from the create body when serverVars is empty', () => {
		const body = buildCreateBody(CredentialType.BEARER_TOKEN, {
			...EMPTY_FORM,
			name: 'T',
			apiVendor: 'acme',
			token: 'x',
			serverVars: {},
		});
		expect('server_variables' in body).toBe(false);
	});

	it('includes server_variables in the update body when serverVars is non-empty', () => {
		const body = buildUpdateBody(
			CredentialType.BEARER_TOKEN,
			{ ...EMPTY_FORM, name: 'Same', serverVars: { domain: 'acme' } },
			'Same',
		);
		expect((body as Record<string, unknown>).server_variables).toEqual({ domain: 'acme' });
	});
});

describe('seedApiKeyFromScheme', () => {
	const schemes = {
		headerKey: { type: 'apiKey', in: 'header', name: 'X-Api-Key' },
		queryKey: { type: 'apiKey', in: 'query', name: 'api_key' },
	};

	it('applies a header location + field name from the scheme', () => {
		const next = seedApiKeyFromScheme({ ...EMPTY_FORM }, schemes, 'headerKey');
		expect(next.location).toBe('header');
		expect(next.fieldName).toBe('X-Api-Key');
	});

	it('applies a query location from the scheme (regression: was dropped)', () => {
		// EMPTY_FORM.location defaults to 'header'; seeding must still pick up the
		// spec-declared `in: 'query'` rather than short-circuiting on the default.
		const next = seedApiKeyFromScheme({ ...EMPTY_FORM }, schemes, 'queryKey');
		expect(next.location).toBe('query');
		expect(next.fieldName).toBe('api_key');
	});

	it('keeps a field name the user already typed but still takes the location', () => {
		const next = seedApiKeyFromScheme(
			{ ...EMPTY_FORM, fieldName: 'My-Custom-Header', location: 'header' },
			schemes,
			'queryKey',
		);
		expect(next.fieldName).toBe('My-Custom-Header');
		expect(next.location).toBe('query');
	});
});
