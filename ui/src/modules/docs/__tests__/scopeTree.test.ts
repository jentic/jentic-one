import { describe, it, expect } from 'vitest';
import {
	authModelCounts,
	buildScopeFamilies,
	endpointCountByScope,
	endpointsForScope,
	indexScopes,
	ownershipEndpoints,
	tierOf,
} from '@/modules/docs/lib/scopeTree';
import type {
	ReferenceEndpoint,
	ReferencePayload,
	ScopeCatalog,
	ScopeEntry,
} from '@/modules/docs/api/types';

function ep(overrides: Partial<ReferenceEndpoint>): ReferenceEndpoint {
	return {
		method: 'GET',
		path: '/x',
		surface: 'core',
		summary: '',
		operation_id: null,
		authenticated: true,
		public: false,
		actor_types: [],
		required_scopes: [],
		implied_scopes: {},
		auth_note: null,
		typical_caller: null,
		group: 'Any authenticated actor',
		...overrides,
	};
}

function scope(overrides: Partial<ScopeEntry> & { name: string }): ScopeEntry {
	return {
		description: '',
		family: overrides.name.split(':', 1)[0],
		action: overrides.name.split(':').pop()!,
		implies: [],
		implies_transitive: [],
		is_superuser: false,
		...overrides,
	};
}

const catalog: ScopeCatalog = {
	schema: 'jentic.scope-catalog/v1',
	total: 4,
	families: [
		{
			name: 'org',
			label: 'Organisation',
			blurb: 'Org admin.',
			scopes: [scope({ name: 'org:admin', action: 'admin', is_superuser: true })],
		},
		{
			name: 'agents',
			label: 'Agents',
			blurb: 'Agent identities.',
			scopes: [
				scope({
					name: 'agents:write',
					action: 'write',
					implies: ['agents:read'],
					implies_transitive: ['agents:read'],
				}),
				scope({ name: 'agents:read', action: 'read' }),
			],
		},
		{
			name: 'capabilities',
			label: 'Capabilities',
			blurb: 'Broker.',
			scopes: [
				scope({
					name: 'capabilities:execute',
					action: 'execute',
					implies: ['apis:read'],
					implies_transitive: ['apis:read'],
				}),
			],
		},
	],
	scopes: [
		scope({ name: 'agents:read', action: 'read' }),
		scope({ name: 'agents:write', action: 'write', implies: ['agents:read'] }),
		scope({ name: 'capabilities:execute', action: 'execute', implies: ['apis:read'] }),
		scope({ name: 'org:admin', action: 'admin', is_superuser: true }),
	],
};

const payload: ReferencePayload = {
	schema: 'jentic.endpoint-scope-tree/v1',
	total: 3,
	groups: [],
	endpoints: [
		ep({ method: 'POST', path: '/agents', required_scopes: ['agents:write'] }),
		ep({ method: 'GET', path: '/agents', required_scopes: ['agents:read'] }),
		ep({ method: 'GET', path: '/agents/{id}', required_scopes: ['agents:read'] }),
	],
	scopes: catalog,
};

describe('tierOf', () => {
	it('maps action (and superuser) to a visual tier', () => {
		expect(tierOf(scope({ name: 'org:admin', action: 'admin', is_superuser: true }))).toBe(
			'admin',
		);
		expect(tierOf(scope({ name: 'agents:write', action: 'write' }))).toBe('write');
		expect(tierOf(scope({ name: 'capabilities:execute', action: 'execute' }))).toBe('execute');
		expect(tierOf(scope({ name: 'agents:read', action: 'read' }))).toBe('read');
	});
});

describe('endpointCountByScope', () => {
	it('counts endpoints per required scope', () => {
		const counts = endpointCountByScope(payload.endpoints);
		expect(counts.get('agents:read')).toBe(2);
		expect(counts.get('agents:write')).toBe(1);
		expect(counts.get('nope')).toBeUndefined();
	});
});

describe('buildScopeFamilies', () => {
	it('augments each scope with tier + endpoint count', () => {
		const families = buildScopeFamilies(payload);
		expect(families).not.toBeNull();
		const agents = families!.find((f) => f.name === 'agents')!;
		const read = agents.scopes.find((s) => s.name === 'agents:read')!;
		const write = agents.scopes.find((s) => s.name === 'agents:write')!;
		expect(read.tier).toBe('read');
		expect(read.endpointCount).toBe(2);
		expect(write.tier).toBe('write');
		expect(write.endpointCount).toBe(1);
	});

	it('counts family endpoints without double-counting an endpoint with two family scopes', () => {
		const families = buildScopeFamilies(payload)!;
		const agents = families.find((f) => f.name === 'agents')!;
		// 3 distinct endpoints touch an agents:* scope.
		expect(agents.endpointCount).toBe(3);
	});

	it('returns null when the payload predates the scope catalogue', () => {
		const { scopes, ...withoutScopes } = payload;
		void scopes;
		expect(buildScopeFamilies(withoutScopes as ReferencePayload)).toBeNull();
	});
});

describe('indexScopes', () => {
	it('indexes the flat scope list by name', () => {
		const idx = indexScopes(payload);
		expect(idx.get('org:admin')?.is_superuser).toBe(true);
		expect(idx.size).toBe(4);
	});

	it('is empty when there is no catalogue', () => {
		const { scopes, ...withoutScopes } = payload;
		void scopes;
		expect(indexScopes(withoutScopes as ReferencePayload).size).toBe(0);
	});
});

describe('endpointsForScope', () => {
	it('returns endpoints requiring a scope, sorted by (path, method)', () => {
		const eps = endpointsForScope(payload, 'agents:read');
		expect(eps.map((e) => `${e.method} ${e.path}`)).toEqual([
			'GET /agents',
			'GET /agents/{id}',
		]);
	});

	it('returns empty for an ungated scope', () => {
		expect(endpointsForScope(payload, 'org:admin')).toEqual([]);
	});
});

describe('authModelCounts', () => {
	it('splits endpoints into scope-gated, ownership-gated, and public', () => {
		const eps = [
			ep({ method: 'POST', path: '/agents', required_scopes: ['agents:write'] }),
			ep({ method: 'GET', path: '/toolkits', required_scopes: [] }), // ownership
			ep({ method: 'GET', path: '/me', required_scopes: [] }), // ownership
			ep({ method: 'GET', path: '/health', authenticated: false, public: true }),
		];
		const counts = authModelCounts(eps);
		expect(counts).toEqual({ scopeGated: 1, ownershipGated: 2, public: 1, total: 4 });
	});
});

describe('ownershipEndpoints', () => {
	it('returns authenticated endpoints with no required scope, sorted', () => {
		const p: ReferencePayload = {
			...payload,
			endpoints: [
				ep({ method: 'POST', path: '/agents', required_scopes: ['agents:write'] }),
				ep({ method: 'GET', path: '/toolkits', required_scopes: [] }),
				ep({ method: 'POST', path: '/toolkits', required_scopes: [] }),
				ep({ method: 'GET', path: '/health', authenticated: false, public: true }),
			],
		};
		const eps = ownershipEndpoints(p);
		expect(eps.map((e) => `${e.method} ${e.path}`)).toEqual([
			'GET /toolkits',
			'POST /toolkits',
		]);
	});
});
