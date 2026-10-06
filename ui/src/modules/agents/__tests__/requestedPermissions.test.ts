/**
 * Which requested permissions the approval card flags: anything that administers
 * the organisation or writes. A miss here lets a risky permission read as routine
 * at the moment it is granted.
 */
import { describe, it, expect } from 'vitest';
import {
	approvalGrant,
	DEFAULT_AGENT_PERMISSIONS,
	permissionRisk,
} from '@/modules/agents/lib/requestedPermissions';

describe('permissionRisk', () => {
	it('flags an admin segment anywhere as admin', () => {
		expect(permissionRisk('org:admin')).toBe('admin');
		expect(permissionRisk('admin:read')).toBe('admin');
	});

	it('flags a trailing write action as write', () => {
		expect(permissionRisk('agents:write')).toBe('write');
		expect(permissionRisk('credentials:write')).toBe('write');
		expect(permissionRisk('Events:WRITE')).toBe('write');
	});

	it('leaves reads, executes and look-alikes unflagged', () => {
		expect(permissionRisk('apis:read')).toBeNull();
		expect(permissionRisk('capabilities:execute')).toBeNull();
		expect(permissionRisk('owner:agents:read')).toBeNull();
		expect(permissionRisk('writers:read')).toBeNull();
		expect(permissionRisk('administrators:read')).toBeNull();
	});
});

describe('DEFAULT_AGENT_PERMISSIONS', () => {
	// Pinned against `DEFAULT_AGENT_PERMISSIONS` in
	// src/jentic_one/shared/auth/permission_catalog.py, which approval grants an
	// agent holding no permissions: a change there must be made here too, and this
	// test makes it visible in review.
	it('mirrors the backend list, in order', () => {
		expect(DEFAULT_AGENT_PERMISSIONS).toEqual([
			'capabilities:execute',
			'capabilities:read',
			'apis:read',
			'catalog:import',
			'executions:read',
			'jobs:read',
			'events:read',
			'owner:resources:read',
			'owner:agents:read',
			'owner:credentials:read',
			'credentials:connect',
		]);
	});
});

describe('approvalGrant', () => {
	const catalogue = ['apis:read', 'agents:write', 'capabilities:execute'];

	it('grants the default agent permissions when none are requested', () => {
		expect(approvalGrant([], catalogue)).toEqual({
			kind: 'defaults',
			granted: DEFAULT_AGENT_PERMISSIONS,
			unrecognised: [],
		});
	});

	it('splits a request into recognised and unrecognised permissions, adding no defaults', () => {
		expect(approvalGrant(['apis:read', 'workflows:write', 'agents:write'], catalogue)).toEqual({
			kind: 'requested',
			granted: ['apis:read', 'agents:write'],
			unrecognised: ['workflows:write'],
		});
	});

	it('grants nothing when every requested permission is unrecognised', () => {
		expect(approvalGrant(['workflows:write', 'toolkits:read'], catalogue)).toEqual({
			kind: 'requested',
			granted: [],
			unrecognised: ['workflows:write', 'toolkits:read'],
		});
	});

	it('counts org:admin as recognised though the catalogue hides it from non-admins', () => {
		expect(approvalGrant(['org:admin'], catalogue).granted).toEqual(['org:admin']);
	});
});
