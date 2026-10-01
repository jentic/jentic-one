/**
 * Which requested scopes the approval card flags: anything that administers the
 * organisation or writes. A miss here lets a risky scope read as routine at the
 * moment it is granted.
 */
import { describe, it, expect } from 'vitest';
import {
	approvalGrant,
	DEFAULT_AGENT_SCOPES,
	scopeRisk,
} from '@/modules/agents/lib/requestedScopes';

describe('scopeRisk', () => {
	it('flags an admin segment anywhere as admin', () => {
		expect(scopeRisk('org:admin')).toBe('admin');
		expect(scopeRisk('admin:read')).toBe('admin');
	});

	it('flags a trailing write action as write', () => {
		expect(scopeRisk('agents:write')).toBe('write');
		expect(scopeRisk('credentials:write')).toBe('write');
		expect(scopeRisk('Events:WRITE')).toBe('write');
	});

	it('leaves reads, executes and look-alikes unflagged', () => {
		expect(scopeRisk('apis:read')).toBeNull();
		expect(scopeRisk('capabilities:execute')).toBeNull();
		expect(scopeRisk('owner:agents:read')).toBeNull();
		expect(scopeRisk('writers:read')).toBeNull();
		expect(scopeRisk('administrators:read')).toBeNull();
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
