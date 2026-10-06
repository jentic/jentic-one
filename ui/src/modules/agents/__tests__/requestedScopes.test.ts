/**
 * Which requested scopes the approval card flags: anything that administers the
 * organisation or writes. A miss here lets a risky scope read as routine at the
 * moment it is granted.
 */
import { describe, it, expect } from 'vitest';
import {
	approvalGrant,
	DEFAULT_AGENT_SCOPES,
	groupScopesByArea,
	scopeArea,
	scopeRisk,
} from '@/modules/agents/lib/requestedScopes';

describe('scopeRisk', () => {
	it('flags any scope mentioning admin as admin', () => {
		expect(scopeRisk('org:admin')).toBe('admin');
		expect(scopeRisk('admin:read')).toBe('admin');
		// Errs towards a flag: a look-alike is flagged too.
		expect(scopeRisk('administrators:read')).toBe('admin');
	});

	it('flags the scopes known to act, by name', () => {
		expect(scopeRisk('overlays:confirm')).toBe('write');
		expect(scopeRisk('credentials:connect')).toBe('write');
		expect(scopeRisk('capabilities:execute')).toBe('execute');
		expect(scopeRisk(' Capabilities:Execute ')).toBe('execute');
	});

	it('falls back to flagging a trailing write action as write', () => {
		expect(scopeRisk('agents:write')).toBe('write');
		expect(scopeRisk('credentials:write')).toBe('write');
		expect(scopeRisk('Events:WRITE')).toBe('write');
	});

	it('leaves reads and look-alikes unflagged', () => {
		expect(scopeRisk('apis:read')).toBeNull();
		expect(scopeRisk('capabilities:read')).toBeNull();
		expect(scopeRisk('owner:agents:read')).toBeNull();
		expect(scopeRisk('writers:read')).toBeNull();
		expect(scopeRisk('constructor')).toBeNull();
	});

	it('flags the acting default agent scopes', () => {
		expect(DEFAULT_AGENT_SCOPES.filter((s) => scopeRisk(s) != null)).toEqual([
			'capabilities:execute',
			'credentials:connect',
		]);
	});
});

describe('DEFAULT_AGENT_SCOPES', () => {
	// Pinned against `DEFAULT_AGENT_SCOPES` in src/jentic_one/shared/scopes.py,
	// which approval grants an agent holding no scopes: a change there must be
	// made here too, and this test makes it visible in review.
	it('mirrors the backend list, in order', () => {
		expect(DEFAULT_AGENT_SCOPES).toEqual([
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

	it('grants the default agent scopes when none are requested', () => {
		expect(approvalGrant([], catalogue)).toEqual({
			kind: 'defaults',
			granted: DEFAULT_AGENT_SCOPES,
			unrecognised: [],
		});
	});

	it('splits a request into recognised and unrecognised scopes, adding no defaults', () => {
		expect(approvalGrant(['apis:read', 'workflows:write', 'agents:write'], catalogue)).toEqual({
			kind: 'requested',
			granted: ['apis:read', 'agents:write'],
			unrecognised: ['workflows:write'],
		});
	});

	it('grants nothing when every requested scope is unrecognised', () => {
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

describe('groupScopesByArea', () => {
	it('groups the default scopes by area, in reading order, keeping their order within', () => {
		expect(groupScopesByArea(DEFAULT_AGENT_SCOPES)).toEqual([
			{ area: 'Capabilities', scopes: ['capabilities:execute', 'capabilities:read'] },
			{ area: 'APIs & catalog', scopes: ['apis:read', 'catalog:import'] },
			{
				area: 'Executions, jobs & events',
				scopes: ['executions:read', 'jobs:read', 'events:read'],
			},
			{ area: 'Credentials', scopes: ['credentials:connect'] },
			{
				area: "Its owner's resources",
				scopes: ['owner:resources:read', 'owner:agents:read', 'owner:credentials:read'],
			},
		]);
	});

	it('puts the organisation scopes together and anything unknown last', () => {
		expect(scopeArea('org:admin')).toBe('Organisation');
		expect(scopeArea('users:read')).toBe('Organisation');
		expect(scopeArea('workflows:write')).toBe('Other');
		// Nothing dropped: every scope lands in exactly one area.
		const scopes = ['workflows:write', 'org:admin', 'apis:read'];
		expect(
			groupScopesByArea(scopes)
				.flatMap((g) => g.scopes)
				.sort(),
		).toEqual([...scopes].sort());
	});
});
