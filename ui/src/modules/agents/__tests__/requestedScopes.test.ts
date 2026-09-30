/**
 * Which requested scopes the approval card flags: anything that administers the
 * organisation or writes. A miss here lets a risky scope read as routine at the
 * moment it is granted.
 */
import { describe, it, expect } from 'vitest';
import { scopeRisk } from '@/modules/agents/lib/requestedScopes';

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
