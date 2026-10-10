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
	 * The Notifications bell's query root (`attentionKeys` in
	 * `shared/attention` derive from this). The bell composes "needs you" from
	 * sibling endpoints, so sibling-module mutations legitimately need to
	 * refresh it — e.g. approving or denying a pending agent (Agents module)
	 * changes what needs you. They invalidate this shared root.
	 */
	attentionRoot: ['attention'] as const,
	/**
	 * @deprecated The retired Dashboard's name for {@link attentionRoot}: the
	 * same key, kept because the enterprise overlay still invalidates it after
	 * deciding a request. Use `attentionRoot`; delete once the overlay moves.
	 */
	dashboardRoot: ['attention'] as const,
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
	 * moment: a CLI agent registers and starts emitting events within seconds,
	 * and every surface resolving its `actor_id` (rail rows, the setup
	 * wizard's header badge) misses and degrades to the raw `agnt_…` id until
	 * the cache expires. Live agent
	 * lifecycle events invalidate this root so the directory refetches the
	 * moment the fleet actually changes.
	 */
	actorDirectoryRoot: ['actor-directory'] as const,
	/**
	 * The Monitor Events feed root (`GET /events` list slices; the Monitor
	 * module's `monitorKeys.events(params)` derives from this). Owned by the
	 * Monitor module, and shared here so cross-module surfaces (rail rows,
	 * failure toasts) can invalidate the Events tab's list slices when a
	 * decision (approve/deny/withdraw) supersedes an actionable event.
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
	/**
	 * The execution-approvals root (the Approvals list and detail slices,
	 * `GET /executions/approvals`). Owned by the Agents module (its
	 * `approvalsKeys` derive from this), but a hold is decided, withdrawn or
	 * expires out-of-band (another reviewer, the agent, the expiry sweep), so
	 * the shared agent-stream provider invalidates this root when an
	 * `execution.approval_*` event arrives.
	 */
	approvalsRoot: ['approvals'] as const,
	/**
	 * The Monitor job slices (`GET /jobs` lists and one job). Owned by the
	 * Monitor module (`monitorKeys.jobs` / `monitorKeys.job` derive from
	 * these), but a held job moves on when its approval settles, which the
	 * agent-stream provider learns from an `execution.approval_*` event and
	 * the Agents module from its own decision.
	 */
	monitorJobsRoot: ['monitor', 'jobs'] as const,
	monitorJobRoot: ['monitor', 'job'] as const,
};
