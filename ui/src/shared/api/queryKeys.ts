/**
 * Cross-module TanStack Query key registry.
 *
 * Each feature module owns its own key factory (`workspaceKeys`,
 * `discoverKeys`, …) for its PRIVATE cache slice. But the ESLint
 * sibling-module boundary means a module cannot import another module's
 * factory, so when one module's mutation must invalidate ANOTHER module's
 * cache the old options were both bad: a raw `['module', …]` literal that
 * silently rots, or over-broad invalidation.
 *
 * This registry owns the few CROSS-CUTTING roots — the contract surface a
 * sibling module legitimately needs to invalidate — so each such key is
 * defined exactly once. The owning module re-uses the root here as the prefix
 * of its own factory, and any other module invalidates through this registry
 * instead of a literal. Renaming a key is then a compile error at every
 * call-site, and the `no-restricted-syntax` lint rule (see eslint.config.js)
 * stops new raw cross-module literals from creeping back in.
 *
 * Module-PRIVATE keys do NOT belong here — keep them in the module's own
 * factory. Add a root here only when a different module must reference it.
 */

/**
 * The Workspace API list (`GET /apis`). Owned by the Workspace module
 * (`workspaceKeys.apis()` derives from this), but the Discover module must
 * invalidate it after a catalog import materializes a new workspace API.
 */
export const sharedQueryKeys = {
	workspaceApis: ['workspace', 'apis'] as const,
	/**
	 * The Dashboard's query root (`dashboardKeys.all` derives from this). The
	 * Dashboard composes its overview from sibling endpoints, so several
	 * sibling-module mutations legitimately need to refresh it — e.g. approving
	 * or denying a pending agent (Agents module) changes the dashboard's
	 * action inbox. Those modules can't import `dashboardKeys`
	 * across the boundary, so they invalidate this shared root instead.
	 */
	dashboardRoot: ['dashboard'] as const,
	/**
	 * The access-request root (`GET /access-requests`). No single module owns it:
	 * the durable approval queue (Dashboard's AccessRequestsPage), the dashboard
	 * action inbox, and the persistent nav badge
	 * (`usePendingAccessRequestCount`) all read slices off this prefix. Every
	 * decision path — the Agent Rail dialog + its Deny fast-path, the dashboard
	 * card, and the queue page — invalidates this root so all three surfaces stay
	 * consistent. Defined once here so the contract is symmetric with
	 * `dashboardRoot` and testable, instead of a bare literal repeated per file.
	 */
	accessRequestsRoot: ['access-requests'] as const,
	/**
	 * The agents root (`GET /agents`). Owned by the Agents module
	 * (`agentsKeys.all` derives from this), but the persistent nav badge
	 * (`usePendingAgentsCount`) reads a `pending`/`count` slice off this prefix
	 * from the shared layer, and the Agents module's approve/deny/create
	 * mutations invalidate this root so the badge updates the instant a pending
	 * agent is decided — without waiting for its fallback poll (#652). Defined
	 * once here so the badge and the module's list cache can't drift apart.
	 */
	agentsRoot: ['agents'] as const,
	/**
	 * The actor directory (`GET /actors`, `useActorDirectory`). Aggressively
	 * cached reference data (5-minute staleTime), which goes stale at the worst
	 * moment: a CLI agent registers and files a provisioning request within
	 * seconds, and every surface resolving its `actor_id` (rail rows, the setup
	 * wizard's header badge) misses and degrades to the raw `agnt_…` id until
	 * the cache expires. Live agent
	 * lifecycle events invalidate this root so the directory refetches the
	 * moment the fleet actually changes.
	 */
	actorDirectoryRoot: ['actor-directory'] as const,
	/**
	 * The Monitor Events feed root (`GET /events` list slices; the Monitor
	 * module's `monitorKeys.events(params)` derives from this). Owned by the
	 * Monitor module, but the shared agent-stream provider's `acknowledge`
	 * (rail rows, failure toasts) flips an event's `acknowledged` flag outside
	 * React Query — without invalidating this root the Events tab's
	 * `status=unacknowledged` list keeps showing a failure the operator already
	 * acked from the rail until its staleTime lapses (#671 follow-through).
	 */
	monitorEventsRoot: ['monitor', 'events'] as const,
	/**
	 * The OAuth-clients root (`GET /admin/oauth-clients`). Owned by the Settings
	 * module (its client + approval-queue slices derive from this), but the
	 * shared agent-stream provider must invalidate it when an `oauth_client.*`
	 * event (DCR registration, approval) or an `oauth_grant.*` event (the rows
	 * carry a per-client active-grant count) lands on the live stream —
	 * otherwise the approval queue sits on its staleTime right after a client
	 * registers.
	 */
	oauthClientsRoot: ['oauth-clients'] as const,
	/**
	 * The OAuth-grants root (per-agent "Connected clients" slices,
	 * `GET /agents/{id}/oauth-grants`, derive from this). The
	 * Agents module owns the panel, but grant creation happens out-of-band (a
	 * consent screen in another tab) and revocation can happen from an admin
	 * surface, so the shared agent-stream provider invalidates this root when
	 * an `oauth_grant.*` event arrives.
	 */
	oauthGrantsRoot: ['oauth-grants'] as const,
};
