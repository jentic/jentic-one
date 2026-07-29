import { describe, expect, it } from 'vitest';
import { partialMatchKey } from '@tanstack/query-core';
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
		// The durable queue, the dashboard action inbox (`ActionInboxBell`), and the
		// nav badge (`pendingAccessRequestCountKey` derives from this) all sit
		// under this prefix; every decision path invalidates it. Lock the literal
		// so a drift can't silently break the cross-surface refresh.
		expect(sharedQueryKeys.accessRequestsRoot).toEqual(['access-requests']);
	});

	it('exposes the toolkits root and the narrower agent-bindings sub-root', () => {
		// `toolkitsRoot` is the toolkits module's whole-cache prefix
		// (`toolkitKeys.all` derives from it); `toolkitAgentsRoot` is the narrow
		// reverse-lookup slice the Agents module's bind/unbind invalidates (#607).
		// Locking both literals keeps the shared prefix and `toolkitKeys.agents`
		// from drifting.
		expect(sharedQueryKeys.toolkitsRoot).toEqual(['toolkits']);
		expect(sharedQueryKeys.toolkitAgentsRoot).toEqual(['toolkits', 'agents']);
	});

	it('exposes the dedicated cross-module toolkit-name cache root', () => {
		// The Agents module caches each bound toolkit's display name under
		// `[...toolkitNameRoot, id]` (#607). It's its OWN top-level root — NOT
		// under `agentsRoot` (so agent lifecycle invalidations don't churn it) and
		// NOT under `toolkitsRoot` (so ordinary toolkit-side mutations that
		// invalidate `['toolkits']` — key rotation, cred bind/unbind, active
		// toggle, create/delete — don't needlessly refetch every bound-row name).
		// Only a rename (`useUpdateToolkit`) invalidates it. Lock the literal so
		// that invariant can't silently drift.
		expect(sharedQueryKeys.toolkitNameRoot).toEqual(['toolkit-name']);
	});

	it('routes the agents-side toolkit-name cache invalidation correctly (#607)', () => {
		// The exact key the Agents module builds for a bound toolkit's name read
		// (`toolkitNameKey(id)` === `[...toolkitNameRoot, id]`). Using TanStack's
		// own prefix matcher proves the routing without spinning up a QueryClient.
		const nameKey = [...sharedQueryKeys.toolkitNameRoot, 'tk_github'] as const;

		// (a) Agent lifecycle mutations invalidate `agentsRoot` (['agents']) —
		// which must NOT reach the cosmetic per-row name cache.
		expect(partialMatchKey(nameKey, sharedQueryKeys.agentsRoot)).toBe(false);

		// (b) Finding #3 — the KEY invariant: the name cache is NOT under the
		// toolkits root, so the many toolkit-side mutations that invalidate
		// `toolkitsRoot` (`['toolkits']`) — key rotation, credential bind/unbind,
		// active toggle, create/delete — do NOT ripple through the name cache.
		expect(partialMatchKey(nameKey, sharedQueryKeys.toolkitsRoot)).toBe(false);

		// (c) A rename DOES refresh it: `useUpdateToolkit` invalidates the shared
		// name root (id-scoped), a prefix of the name key, so the renamed
		// toolkit's cached label refreshes instantly.
		expect(partialMatchKey(nameKey, sharedQueryKeys.toolkitNameRoot)).toBe(true);
		expect(partialMatchKey(nameKey, [...sharedQueryKeys.toolkitNameRoot, 'tk_github'])).toBe(
			true,
		);
	});

	it('exposes the actor-directory root', () => {
		// `useActorDirectory` caches the directory aggressively as reference
		// data; the SSE→query bridge invalidates this root when an agent
		// registers so freshly-registered agents resolve to names, not raw ids.
		expect(sharedQueryKeys.actorDirectoryRoot).toEqual(['actor-directory']);
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
