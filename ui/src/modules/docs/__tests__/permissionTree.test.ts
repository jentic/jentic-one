import { describe, it, expect } from 'vitest';
import {
	authModelCounts,
	buildPermissionFamilies,
	endpointCountByPermission,
	endpointsForPermission,
	indexPermissions,
	ownershipEndpoints,
	tierOf,
} from '@/modules/docs/lib/permissionTree';
import type {
	ReferenceEndpoint,
	ReferencePayload,
	PermissionCatalog,
	PermissionEntry,
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
		required_permissions: [],
		implied_permissions: {},
		auth_note: null,
		typical_caller: null,
		group: 'Any authenticated actor',
		...overrides,
	};
}

function permission(overrides: Partial<PermissionEntry> & { name: string }): PermissionEntry {
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

const catalog: PermissionCatalog = {
	schema: 'jentic.permission-catalog/v1',
	total: 4,
	families: [
		{
			name: 'org',
			label: 'Organisation',
			blurb: 'Org admin.',
			permissions: [permission({ name: 'org:admin', action: 'admin', is_superuser: true })],
		},
		{
			name: 'agents',
			label: 'Agents',
			blurb: 'Agent identities.',
			permissions: [
				permission({
					name: 'agents:write',
					action: 'write',
					implies: ['agents:read'],
					implies_transitive: ['agents:read'],
				}),
				permission({ name: 'agents:read', action: 'read' }),
			],
		},
		{
			name: 'capabilities',
			label: 'Capabilities',
			blurb: 'Broker.',
			permissions: [
				permission({
					name: 'capabilities:execute',
					action: 'execute',
					implies: ['apis:read'],
					implies_transitive: ['apis:read'],
				}),
			],
		},
	],
	permissions: [
		permission({ name: 'agents:read', action: 'read' }),
		permission({ name: 'agents:write', action: 'write', implies: ['agents:read'] }),
		permission({ name: 'capabilities:execute', action: 'execute', implies: ['apis:read'] }),
		permission({ name: 'org:admin', action: 'admin', is_superuser: true }),
	],
};

const payload: ReferencePayload = {
	schema: 'jentic.endpoint-permission-tree/v1',
	total: 3,
	groups: [],
	endpoints: [
		ep({ method: 'POST', path: '/agents', required_permissions: ['agents:write'] }),
		ep({ method: 'GET', path: '/agents', required_permissions: ['agents:read'] }),
		ep({ method: 'GET', path: '/agents/{id}', required_permissions: ['agents:read'] }),
	],
	permissions: catalog,
};

describe('tierOf', () => {
	it('maps action (and superuser) to a visual tier', () => {
		expect(tierOf(permission({ name: 'org:admin', action: 'admin', is_superuser: true }))).toBe(
			'admin',
		);
		expect(tierOf(permission({ name: 'agents:write', action: 'write' }))).toBe('write');
		expect(tierOf(permission({ name: 'capabilities:execute', action: 'execute' }))).toBe(
			'execute',
		);
		expect(tierOf(permission({ name: 'agents:read', action: 'read' }))).toBe('read');
	});
});

describe('endpointCountByPermission', () => {
	it('counts endpoints per required permission', () => {
		const counts = endpointCountByPermission(payload.endpoints);
		expect(counts.get('agents:read')).toBe(2);
		expect(counts.get('agents:write')).toBe(1);
		expect(counts.get('nope')).toBeUndefined();
	});
});

describe('buildPermissionFamilies', () => {
	it('augments each permission with tier + endpoint count', () => {
		const families = buildPermissionFamilies(payload);
		expect(families).not.toBeNull();
		const agents = families!.find((f) => f.name === 'agents')!;
		const read = agents.permissions.find((s) => s.name === 'agents:read')!;
		const write = agents.permissions.find((s) => s.name === 'agents:write')!;
		expect(read.tier).toBe('read');
		expect(read.endpointCount).toBe(2);
		expect(write.tier).toBe('write');
		expect(write.endpointCount).toBe(1);
	});

	it('counts family endpoints without double-counting an endpoint with two family permissions', () => {
		const families = buildPermissionFamilies(payload)!;
		const agents = families.find((f) => f.name === 'agents')!;
		// 3 distinct endpoints touch an agents:* permission.
		expect(agents.endpointCount).toBe(3);
	});

	it('returns null when the payload predates the permission catalogue', () => {
		const { permissions, ...withoutPermissions } = payload;
		void permissions;
		expect(buildPermissionFamilies(withoutPermissions as ReferencePayload)).toBeNull();
	});
});

describe('indexPermissions', () => {
	it('indexes the flat permission list by name', () => {
		const idx = indexPermissions(payload);
		expect(idx.get('org:admin')?.is_superuser).toBe(true);
		expect(idx.size).toBe(4);
	});

	it('is empty when there is no catalogue', () => {
		const { permissions, ...withoutPermissions } = payload;
		void permissions;
		expect(indexPermissions(withoutPermissions as ReferencePayload).size).toBe(0);
	});
});

describe('endpointsForPermission', () => {
	it('returns endpoints requiring a permission, sorted by (path, method)', () => {
		const eps = endpointsForPermission(payload, 'agents:read');
		expect(eps.map((e) => `${e.method} ${e.path}`)).toEqual([
			'GET /agents',
			'GET /agents/{id}',
		]);
	});

	it('returns empty for an ungated permission', () => {
		expect(endpointsForPermission(payload, 'org:admin')).toEqual([]);
	});
});

describe('authModelCounts', () => {
	it('splits endpoints into permission-gated, ownership-gated, and public', () => {
		const eps = [
			ep({ method: 'POST', path: '/agents', required_permissions: ['agents:write'] }),
			ep({ method: 'GET', path: '/gadgets', required_permissions: [] }), // ownership
			ep({ method: 'GET', path: '/me', required_permissions: [] }), // ownership
			ep({ method: 'GET', path: '/health', authenticated: false, public: true }),
		];
		const counts = authModelCounts(eps);
		expect(counts).toEqual({ permissionGated: 1, ownershipGated: 2, public: 1, total: 4 });
	});
});

describe('ownershipEndpoints', () => {
	it('returns authenticated endpoints with no required permission, sorted', () => {
		const p: ReferencePayload = {
			...payload,
			endpoints: [
				ep({ method: 'POST', path: '/agents', required_permissions: ['agents:write'] }),
				ep({ method: 'GET', path: '/gadgets', required_permissions: [] }),
				ep({ method: 'POST', path: '/gadgets', required_permissions: [] }),
				ep({ method: 'GET', path: '/health', authenticated: false, public: true }),
			],
		};
		const eps = ownershipEndpoints(p);
		expect(eps.map((e) => `${e.method} ${e.path}`)).toEqual(['GET /gadgets', 'POST /gadgets']);
	});
});
