import { describe, expect, it } from 'vitest';
import {
	ACCESS_REQUEST_STATUS_VARIANT,
	isScopeGrant,
	isSpecificResource,
	isUnrestrictedAllow,
	itemTargetLabel,
	parseItemRules,
	ruleSummary,
	scopeLabel,
	summarizeAccessRequest,
	type AccessRequest,
	type AccessRequestItem,
	type PermissionRule,
} from '../accessRequests';

/** Minimal item factory — only the fields under test matter. */
function item(overrides: Partial<AccessRequestItem> = {}): AccessRequestItem {
	return {
		id: 'ari_x',
		resource_type: 'credential',
		action: 'bind',
		status: 'pending',
		...overrides,
	};
}

/** Minimal envelope factory for the queue-summary helper. */
function request(items: AccessRequestItem[]): AccessRequest {
	return {
		id: 'areq_x',
		actor_id: 'agnt_x',
		status: 'pending',
		requested_by: 'agnt_x',
		created_by: 'agnt_x',
		approve_url: 'https://app.example.test/access-requests/areq_x',
		filed_at: '2026-07-23T09:00:00Z',
		expires_at: '2026-07-30T09:00:00Z',
		items,
	};
}

describe('itemTargetLabel', () => {
	it('prefers an explicit resource_id', () => {
		expect(itemTargetLabel(item({ resource_id: 'cred_123' }))).toBe('cred_123');
	});

	it('builds a vendor/name@version label from a resource_reference', () => {
		expect(
			itemTargetLabel(
				item({
					resource_id: null,
					resource_reference: { vendor: 'github', name: 'rest', version: '1.0' },
				}),
			),
		).toBe('github/rest@1.0');
	});

	it('falls back to api_reference, then resource_type', () => {
		expect(
			itemTargetLabel(
				item({ resource_id: null, resource_reference: { api_reference: 'gh.repos' } }),
			),
		).toBe('gh.repos');
		expect(itemTargetLabel(item({ resource_id: null, resource_type: 'toolkit' }))).toBe(
			'toolkit',
		);
	});
});

describe('isSpecificResource', () => {
	it('is true only when a concrete resource_id is present', () => {
		expect(isSpecificResource(item({ resource_id: 'cred_1' }))).toBe(true);
		expect(isSpecificResource(item({ resource_id: null }))).toBe(false);
	});
});

describe('isScopeGrant / scopeLabel', () => {
	it('detects scope.grant items', () => {
		expect(isScopeGrant(item({ resource_type: 'scope', action: 'grant' }))).toBe(true);
		expect(isScopeGrant(item({ resource_type: 'credential', action: 'bind' }))).toBe(false);
		expect(isScopeGrant(item({ resource_type: 'scope', action: 'use' }))).toBe(false);
	});

	it('surfaces the scope string from resource_id', () => {
		expect(
			scopeLabel(
				item({
					resource_type: 'scope',
					action: 'grant',
					resource_id: 'capabilities:execute',
				}),
			),
		).toBe('capabilities:execute');
	});

	it('falls back to resource_type when no scope id present', () => {
		expect(
			scopeLabel(item({ resource_type: 'scope', action: 'grant', resource_id: null })),
		).toBe('scope');
	});
});

describe('parseItemRules', () => {
	it('returns [] when rules are absent or not an array', () => {
		expect(parseItemRules(item({ rules: null }))).toEqual([]);
		expect(parseItemRules(item({ rules: undefined }))).toEqual([]);
		expect(
			parseItemRules(item({ rules: 'nope' as unknown as Record<string, unknown>[] })),
		).toEqual([]);
	});

	it('coerces valid rules and normalises optional fields to null', () => {
		const rules = parseItemRules(
			item({
				rules: [
					{ effect: 'allow', methods: ['GET', 'POST'], operations: ['repos/get'] },
					{ effect: 'deny' },
					{ effect: 'require-approval', path: '/admin/*' },
				],
			}),
		);
		expect(rules).toEqual<PermissionRule[]>([
			{ effect: 'allow', methods: ['GET', 'POST'], path: null, operations: ['repos/get'] },
			{ effect: 'deny', methods: null, path: null, operations: null },
			{ effect: 'require-approval', methods: null, path: '/admin/*', operations: null },
		]);
	});

	it('drops malformed entries (bad/missing effect, non-objects)', () => {
		const rules = parseItemRules(
			item({
				rules: [
					{ effect: 'maybe' } as unknown as Record<string, unknown>,
					{ methods: ['GET'] } as unknown as Record<string, unknown>,
					null as unknown as Record<string, unknown>,
					{ effect: 'allow' },
				],
			}),
		);
		expect(rules).toEqual<PermissionRule[]>([
			{ effect: 'allow', methods: null, path: null, operations: null },
		]);
	});

	it('drops non-string elements from methods/operations arrays', () => {
		const rules = parseItemRules(
			item({
				rules: [
					{
						effect: 'allow',
						methods: ['GET', 1, null, 'POST'] as unknown as string[],
						operations: [{}, 'repos/get', 2] as unknown as string[],
					},
				],
			}),
		);
		expect(rules).toEqual<PermissionRule[]>([
			{ effect: 'allow', methods: ['GET', 'POST'], path: null, operations: ['repos/get'] },
		]);
	});
});

describe('isUnrestrictedAllow', () => {
	it('is true only for an allow with no methods, path, or operations', () => {
		expect(
			isUnrestrictedAllow({ effect: 'allow', methods: null, path: null, operations: null }),
		).toBe(true);
		expect(
			isUnrestrictedAllow({ effect: 'allow', methods: [], path: null, operations: [] }),
		).toBe(true);
	});

	it('is false for a constrained allow or any non-allow effect', () => {
		expect(
			isUnrestrictedAllow({
				effect: 'allow',
				methods: ['GET'],
				path: null,
				operations: null,
			}),
		).toBe(false);
		expect(
			isUnrestrictedAllow({
				effect: 'allow',
				methods: null,
				path: '/v1/*',
				operations: null,
			}),
		).toBe(false);
		expect(
			isUnrestrictedAllow({ effect: 'deny', methods: null, path: null, operations: null }),
		).toBe(false);
		expect(
			isUnrestrictedAllow({
				effect: 'require-approval',
				methods: null,
				path: null,
				operations: null,
			}),
		).toBe(false);
	});
});

describe('ruleSummary', () => {
	it('describes an empty rule set as unrestricted', () => {
		expect(ruleSummary([])).toBe('No operation restrictions — full access to the resource.');
	});

	it('flags a condition-less allow as unrestricted', () => {
		expect(
			ruleSummary([{ effect: 'allow', methods: null, path: null, operations: null }]),
		).toBe('Allows ANY request (unrestricted).');
	});

	it('summarises allow/deny with methods, operations and path', () => {
		expect(
			ruleSummary([
				{
					effect: 'allow',
					methods: ['GET', 'POST'],
					operations: ['a', 'b', 'c'],
					path: null,
				},
			]),
		).toBe('Allows GET, POST on 3 operations.');
		expect(
			ruleSummary([{ effect: 'deny', methods: ['DELETE'], operations: null, path: null }]),
		).toBe('Blocks DELETE.');
		expect(
			ruleSummary([{ effect: 'allow', methods: null, operations: ['x'], path: '/v1/*' }]),
		).toBe('Allows 1 operation, scoped to path /v1/*.');
	});

	it('announces blocks before allows and falls back to "all requests"', () => {
		expect(
			ruleSummary([
				{ effect: 'allow', methods: ['GET'], operations: null, path: null },
				{ effect: 'deny', methods: null, operations: null, path: null },
			]),
		).toBe('Blocks all requests; Allows GET.');
	});
});

describe('summarizeAccessRequest', () => {
	it('renders "type · action" for a single item', () => {
		expect(summarizeAccessRequest(request([item()]))).toBe('credential · bind');
	});

	it('appends "+N more" for multi-item requests', () => {
		expect(
			summarizeAccessRequest(
				request([
					item({ resource_type: 'toolkit', action: 'create' }),
					item({ id: 'ari_2' }),
					item({ id: 'ari_3' }),
				]),
			),
		).toBe('toolkit · create +2 more');
	});

	it('falls back to "access" for an empty item list', () => {
		expect(summarizeAccessRequest(request([]))).toBe('access');
	});
});

describe('ACCESS_REQUEST_STATUS_VARIANT', () => {
	it('covers every backend status, including the view-time derived "expired"', () => {
		expect(ACCESS_REQUEST_STATUS_VARIANT).toEqual({
			pending: 'pending',
			approved: 'success',
			denied: 'danger',
			partially_approved: 'warning',
			expired: 'warning',
			withdrawn: 'default',
		});
	});

	it('lets consumers fall back to "default" for unknown statuses', () => {
		expect(ACCESS_REQUEST_STATUS_VARIANT['bogus'] ?? 'default').toBe('default');
	});
});
