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
	 * or denying a pending agent (Agents module) changes the "Awaiting approval"
	 * tile + PendingAgentsCard. Those modules can't import `dashboardKeys`
	 * across the boundary, so they invalidate this shared root instead.
	 */
	dashboardRoot: ['dashboard'] as const,
	/**
	 * The access-request root (`GET /access-requests`). No single module owns it:
	 * the durable approval queue (Dashboard's AccessRequestsPage), the dashboard
	 * PendingAccessRequestsCard, and the persistent nav badge
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
	 * The toolkits root (`GET /toolkits`). Owned by the Toolkits module
	 * (`toolkitKeys.all` derives from this), but the Agents module's agent-side
	 * bind/unbind mutations (#607) legitimately need to invalidate it: binding a
	 * toolkit to an agent changes the toolkit-side "Bound Agents" card, which
	 * lives under this prefix. The Agents module can't import `toolkitKeys`
	 * across the sibling-module boundary, so it invalidates this shared root
	 * instead. Defined once here so the toolkit detail's bound-agents view and
	 * the module's cache can't drift apart.
	 */
	toolkitsRoot: ['toolkits'] as const,
	/**
	 * The agent↔toolkit binding sub-root (`GET /toolkits/{id}/agents`). Owned by
	 * the Toolkits module (`toolkitKeys.agents(id)` derives from this), but the
	 * Agents module's agent-side bind/unbind mutations (#607) need to refresh the
	 * toolkit-side "Bound Agents" card after a binding changes. Narrower than
	 * `toolkitsRoot`: invalidating this prefix refreshes only the reverse-lookup
	 * slices, not every mounted toolkits query (list, detail, keys, bindings, …).
	 * Defined once here so the shared prefix and the module factory can't drift.
	 */
	toolkitAgentsRoot: ['toolkits', 'agents'] as const,
};
