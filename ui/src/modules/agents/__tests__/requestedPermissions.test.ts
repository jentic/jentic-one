/**
 * Which requested permissions the approval card flags: anything that administers
 * the organisation, writes or runs upstream calls. A miss here lets a risky permission read as routine
 * at the moment it is granted.
 */
import { describe, it, expect } from 'vitest';
import {
	approvalGrant,
	DEFAULT_AGENT_PERMISSIONS,
	groupPermissionsByArea,
	permissionArea,
	permissionRisk,
} from '@/modules/agents/lib/requestedPermissions';

describe('permissionRisk', () => {
	it('flags any permission mentioning admin as admin', () => {
		expect(permissionRisk('org:admin')).toBe('admin');
		expect(permissionRisk('admin:read')).toBe('admin');
		// Errs towards a flag: a look-alike is flagged too.
		expect(permissionRisk('administrators:read')).toBe('admin');
	});

	it('flags the permissions known to act, by name', () => {
		expect(permissionRisk('overlays:confirm')).toBe('write');
		expect(permissionRisk('credentials:connect')).toBe('write');
		expect(permissionRisk('capabilities:execute')).toBe('execute');
		expect(permissionRisk(' Capabilities:Execute ')).toBe('execute');
	});

	it('falls back to flagging a trailing write action as write', () => {
		expect(permissionRisk('agents:write')).toBe('write');
		expect(permissionRisk('credentials:write')).toBe('write');
		expect(permissionRisk('Events:WRITE')).toBe('write');
	});

	it('leaves reads and look-alikes unflagged', () => {
		expect(permissionRisk('apis:read')).toBeNull();
		expect(permissionRisk('capabilities:read')).toBeNull();
		expect(permissionRisk('owner:agents:read')).toBeNull();
		expect(permissionRisk('writers:read')).toBeNull();
		expect(permissionRisk('constructor')).toBeNull();
	});

	it('flags the acting default agent permissions', () => {
		expect(DEFAULT_AGENT_PERMISSIONS.filter((p) => permissionRisk(p) != null)).toEqual([
			'capabilities:execute',
			'credentials:connect',
		]);
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

describe('groupPermissionsByArea', () => {
	it('groups the default permissions by area, in reading order, keeping their order within', () => {
		expect(groupPermissionsByArea(DEFAULT_AGENT_PERMISSIONS)).toEqual([
			{ area: 'Capabilities', permissions: ['capabilities:execute', 'capabilities:read'] },
			{ area: 'APIs & catalog', permissions: ['apis:read', 'catalog:import'] },
			{
				area: 'Executions, jobs & events',
				permissions: ['executions:read', 'jobs:read', 'events:read'],
			},
			{ area: 'Credentials', permissions: ['credentials:connect'] },
			{
				area: "Its owner's resources",
				permissions: [
					'owner:resources:read',
					'owner:agents:read',
					'owner:credentials:read',
				],
			},
		]);
	});

	it('puts the organisation permissions together and anything unknown last', () => {
		expect(permissionArea('org:admin')).toBe('Organisation');
		expect(permissionArea('users:read')).toBe('Organisation');
		expect(permissionArea('workflows:write')).toBe('Other');
		// Nothing dropped: every permission lands in exactly one area.
		const permissions = ['workflows:write', 'org:admin', 'apis:read'];
		expect(
			groupPermissionsByArea(permissions)
				.flatMap((g) => g.permissions)
				.sort(),
		).toEqual([...permissions].sort());
	});
});
