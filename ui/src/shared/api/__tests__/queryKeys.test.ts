import { describe, expect, it } from 'vitest';
import { sharedQueryKeys } from '@/shared/api/queryKeys';

/**
 * Pins the cross-module key registry. These roots are a contract consumed by
 * sibling modules (which can't import each other), so a change here ripples
 * across the app — this test makes that change deliberate. The registry must
 * hold only genuinely cross-cutting roots; module-private keys belong in the
 * owning module's own factory.
 */
describe('sharedQueryKeys', () => {
	it('exposes the workspace API-list root', () => {
		// The literal here is the LOCK, not a duplication to DRY away: restating
		// the value verbatim is what forces a value change to be a deliberate,
		// failing edit (a drifted key silently breaks cross-module invalidation).
		expect(sharedQueryKeys.workspaceApis).toEqual(['workspace', 'apis']);
	});

	it('exposes the dashboard root', () => {
		// Derived by the Dashboard's own `dashboardKeys.all` and invalidated by
		// the shared SSE→query bridge (`agentStream`) on every approval event.
		// Locking the literal keeps that cross-layer invalidation from drifting.
		expect(sharedQueryKeys.dashboardRoot).toEqual(['dashboard']);
	});

	it('exposes the access-request root', () => {
		// The durable queue, the dashboard PendingAccessRequestsCard, and the
		// nav badge (`pendingAccessRequestCountKey` derives from this) all sit
		// under this prefix; every decision path invalidates it. Lock the literal
		// so a drift can't silently break the cross-surface refresh.
		expect(sharedQueryKeys.accessRequestsRoot).toEqual(['access-requests']);
	});

	it('keeps every registered root a non-empty string array', () => {
		for (const [name, key] of Object.entries(sharedQueryKeys)) {
			expect(Array.isArray(key), `${name} must be an array`).toBe(true);
			expect(key.length, `${name} must be non-empty`).toBeGreaterThan(0);
			for (const segment of key) {
				expect(typeof segment, `${name} segments must be strings`).toBe('string');
			}
		}
	});
});
