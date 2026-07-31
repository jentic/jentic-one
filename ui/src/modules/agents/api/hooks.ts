/**
 * Agents service tier — TanStack Query hooks.
 *
 * The only backend access path for Agents views: components/pages call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views must never reach past this layer (ESLint-enforced).
 *
 * Lifecycle mutations follow the verified response contract: approve/deny
 * return the updated row (we seed the detail cache from it), while
 * disable/enable/archive return 204, so those invalidate the affected slices to
 * force a refetch.
 */
import {
	keepPreviousData,
	useInfiniteQuery,
	useMutation,
	useQuery,
	useQueryClient,
} from '@tanstack/react-query';
import { useCallback } from 'react';
import { toast } from '@/shared/ui';
import {
	approveAgent,
	approveServiceAccount,
	archiveAgent,
	archiveServiceAccount,
	createAgent,
	createServiceAccount,
	updateAgent,
	type AgentPatch,
	denyAgent,
	denyServiceAccount,
	disableAgent,
	disableServiceAccount,
	enableAgent,
	enableServiceAccount,
	generateAgentApiKey,
	generateServiceAccountApiKey,
	getAgent,
	getAgentApiKeyHistory,
	getAgentApiKeyInfo,
	getAgentScopes,
	getServiceAccount,
	getServiceAccountScopes,
	getToolkitName,
	listAgentToolkits,
	listAgents,
	listLinkableToolkits,
	listPermissions,
	listServiceAccounts,
	replaceAgentScopes,
	replaceServiceAccountScopes,
	revokeAgentApiKey,
	bindToolkitToAgent,
	unbindToolkitFromAgent,
	fetchActorAccessRequests,
	fetchActorsUsage,
	fetchActorUsageDetail,
	fetchActorExecutions,
	listActorAudit,
	type ActorAuditEntry,
	type ActorUsage,
	type ActorUsageDetail,
	type ActorExecutionEntity,
	type ListResult,
} from '@/modules/agents/api/client';
import type {
	AgentEntity,
	ApiKeyHistoryEntry,
	ApiKeyInfoEntity,
	ApiKeyResult,
	LinkableToolkit,
	PermissionCatalogEntry,
	ServiceAccountEntity,
	ToolkitBindingEntity,
} from '@/modules/agents/api/types';
import type { AccessRequest } from '@/shared/lib';
import { sharedQueryKeys } from '@/shared/api';

/** Stable query-key roots so callers/tests can target invalidation precisely.
 * `all` derives from the shared cross-module registry so the persistent nav
 * badge (`usePendingAgentsCount`) and this factory share one `agents` prefix and
 * can't drift (#652). */
const agentsKeys = {
	all: sharedQueryKeys.agentsRoot,
	lists: () => [...agentsKeys.all, 'list'] as const,
	list: (status: string) => [...agentsKeys.all, 'list', status] as const,
	detail: (id: string) => [...agentsKeys.all, 'detail', id] as const,
	toolkits: (id: string) => [...agentsKeys.all, 'toolkits', id] as const,
	apiKeyInfo: (id: string) => [...agentsKeys.all, 'api-key-info', id] as const,
	apiKeyHistory: (id: string) => [...agentsKeys.all, 'api-key-history', id] as const,
	scopes: (id: string) => [...agentsKeys.all, 'scopes', id] as const,
};

/** Test-only handle on the agents key factory so the cross-module-key guard
 * (#511/#652) can pin `agentsKeys.all` to `sharedQueryKeys.agentsRoot` without
 * widening the module's public surface. Not for production use. */
export const agentsKeysForTest = agentsKeys;

const serviceAccountKeys = {
	all: ['service-accounts'] as const,
	lists: () => [...serviceAccountKeys.all, 'list'] as const,
	list: (status: string) => [...serviceAccountKeys.all, 'list', status] as const,
	detail: (id: string) => [...serviceAccountKeys.all, 'detail', id] as const,
	scopes: (id: string) => [...serviceAccountKeys.all, 'scopes', id] as const,
};

/**
 * Platform permission catalogue (`GET /permissions`). Module-private (the
 * agents module is the only UI consumer of the catalogue today) and read-only,
 * so it lives in this factory rather than the cross-module `sharedQueryKeys`
 * registry. The `agents` root already owns the `permissions` namespace here.
 */
const permissionsKey = [...agentsKeys.all, 'permissions'] as const;

/**
 * Candidate toolkits for the agent-side "Bind toolkit" picker (#607). Kept
 * under **its own root** (not ``agentsKeys.all``) so a broad
 * ``sharedQueryKeys.agentsRoot`` invalidation — used by approve/deny/create —
 * doesn't pointlessly refetch ``GET /toolkits``. Mirrors the
 * toolkits module keeping its ``linkableAgents`` cache under its own toolkits
 * root for the same reason.
 */
const linkableToolkitsKey = ['agents-linkable-toolkits'] as const;

/**
 * Human name for a SINGLE bound toolkit, keyed by its id. Powers per-row name
 * resolution on the detail page's "Bound toolkits" card (#607): each row reads
 * `GET /toolkits/{id}` for just its own name instead of the whole workspace
 * paying `useLinkableToolkits`' paginated `GET /toolkits` sweep on every page
 * load (which would also defeat the picker dialog's `enabled` gate).
 *
 * Keyed under the shared `toolkitNameRoot` (`['toolkit-name',id]`) — its OWN
 * top-level root, NOT under `agentsRoot` and NOT under `toolkitsRoot`. That
 * isolation is deliberate: (a) agent lifecycle mutations (approve/deny/create)
 * invalidate `sharedQueryKeys.agentsRoot` and must NOT refetch every visible
 * bound toolkit's cosmetic name; and (b) ordinary toolkit-side mutations (key
 * rotation, credential bind/unbind, active toggle, create/delete) invalidate
 * `toolkitKeys.all` (`['toolkits']`) but leave a toolkit's NAME unchanged, so
 * they must not ripple here either. The one event that changes a name — a
 * rename via the Toolkits module's `useUpdateToolkit` — invalidates this shared
 * root (id-scoped), so a renamed toolkit's cached label refreshes instantly.
 */
const toolkitNameKey = (toolkitId: string) =>
	[...sharedQueryKeys.toolkitNameRoot, toolkitId] as const;

/**
 * Access requests filed BY an actor (#619), keyed by the actor's id + status.
 * `actor_id` is globally unique across agents and service accounts, so one key
 * factory serves both detail pages.
 */
export const actorAccessRequestsKey = (actorId: string, status: string) =>
	['access-requests', 'by-actor', actorId, status] as const;

/**
 * Prefix key covering EVERY status slice for one actor. A decision moves a
 * request between the pending / approved / denied / all views, so invalidating
 * this root refreshes them all in one call — and keeps the key shape owned here
 * (the single source of truth) rather than hand-written at the call site.
 */
export const actorAccessRequestsRootKey = (actorId: string) =>
	['access-requests', 'by-actor', actorId] as const;

function notifyError(error: unknown, fallback: string): void {
	toast({
		title: fallback,
		description: error instanceof Error ? error.message : undefined,
		variant: 'error',
	});
}

// ---------------------------------------------------------------------------
// Agents — queries
// ---------------------------------------------------------------------------

/**
 * The cursor-paginated agents list. An infinite query so the page can render
 * the first 50-row page immediately and "Load more" through `next_cursor`
 * (the backend caps `limit` at 200; we keep the default 50 per page). Status
 * narrowing is client-side on the loaded pages (the page always fetches
 * `all`), so one cache entry serves every status segment.
 */
export function useAgents(params: { status?: string } = {}) {
	const status = params.status ?? 'all';
	return useInfiniteQuery<ListResult<AgentEntity>>({
		queryKey: agentsKeys.list(status),
		queryFn: ({ pageParam }) =>
			listAgents({
				status: status === 'all' ? null : status,
				cursor: (pageParam as string | null) ?? null,
			}),
		initialPageParam: null,
		getNextPageParam: (last) => (last.hasMore ? last.nextCursor : null),
		placeholderData: keepPreviousData,
	});
}

export function useAgent(id: string | null) {
	return useQuery<AgentEntity>({
		queryKey: agentsKeys.detail(id ?? ''),
		queryFn: () => getAgent(id as string),
		enabled: id != null,
	});
}

export function useAgentToolkits(id: string | null) {
	return useQuery<ToolkitBindingEntity[]>({
		queryKey: agentsKeys.toolkits(id ?? ''),
		queryFn: () => listAgentToolkits(id as string),
		enabled: id != null,
	});
}

/**
 * Candidate toolkits for the agent-side "Bind toolkit" picker (#607). Fetched
 * only while the dialog is open (``enabled``) so it costs nothing on the rest
 * of the detail page. Keyed under its own root (``linkableToolkitsKey``) rather
 * than ``agentsKeys.all`` so a broad ``sharedQueryKeys.agentsRoot``
 * invalidation (used by approve/deny/create) does not pointlessly refetch
 * ``GET /toolkits``.
 */
export function useLinkableToolkits({ enabled = true }: { enabled?: boolean } = {}) {
	return useQuery<LinkableToolkit[]>({
		queryKey: linkableToolkitsKey,
		queryFn: () => listLinkableToolkits(),
		enabled,
	});
}

/**
 * Resolve one bound toolkit's human name (`GET /toolkits/{id}`), safe to call
 * once per bound row (#607). Names are slow-changing, so it's cached generously
 * (5 min) — the card can mount many of these without a thundering herd. Returns
 * ``null`` for a since-deleted / not-found toolkit so the row falls back to the
 * id. Disabled until an id is present.
 */
export function useToolkitName(toolkitId: string | null) {
	return useQuery<string | null>({
		queryKey: toolkitNameKey(toolkitId ?? ''),
		queryFn: () => getToolkitName(toolkitId as string),
		enabled: toolkitId != null,
		staleTime: 5 * 60 * 1000,
	});
}

/**
 * Bind/unbind an agent↔toolkit (#607) ripples across three surfaces: the
 * agent's own bound-toolkits list, the picker's candidate list (the just-bound
 * toolkit becomes ineligible), and the toolkit-side "Bound Agents" card (the
 * binding is bidirectional). Invalidate them together so none goes stale.
 * Mirrors the sibling `useInvalidateToolkitSurfaces` in the toolkits module.
 *
 * The toolkit-side card is refreshed via the narrow shared
 * `toolkitAgentsRoot` (`['toolkits','agents']`) — the reverse-lookup slices
 * only — rather than the whole `toolkitsRoot`, which would needlessly refetch
 * every mounted toolkits query (list, detail, keys, bindings). Null-guards the
 * agent id so a call before the agent resolves is a no-op on the agent slice.
 */
function useInvalidateAgentBindingSurfaces(agentId: string | null) {
	const qc = useQueryClient();
	// Memoised on [agentId, qc] so the returned handle keeps a stable identity
	// across renders — a caller can safely store it in a memoised child's props
	// or an effect dependency list without re-running on every render.
	return useCallback(() => {
		if (agentId) qc.invalidateQueries({ queryKey: agentsKeys.toolkits(agentId) });
		qc.invalidateQueries({ queryKey: linkableToolkitsKey });
		qc.invalidateQueries({ queryKey: sharedQueryKeys.toolkitAgentsRoot });
	}, [agentId, qc]);
}

/** Bind a toolkit to this agent (#607) — refreshes both the agent's bound
 * toolkits list and the picker's candidates list on success. Mirrors the
 * toolkit page's "Link agent". Accepts a nullable agent id and refuses to fire
 * without one so a stray call before the agent has resolved cannot POST to
 * ``/agents//toolkits``. */
export function useBindToolkitToAgent(agentId: string | null) {
	const invalidate = useInvalidateAgentBindingSurfaces(agentId);
	return useMutation<void, Error, string>({
		mutationFn: (toolkitId: string) => {
			if (!agentId) {
				return Promise.reject(new Error('Cannot bind a toolkit before the agent loads.'));
			}
			return bindToolkitToAgent(agentId, toolkitId);
		},
		onSuccess: () => {
			invalidate();
			toast({ title: 'Toolkit bound', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to bind the toolkit.'),
	});
}

/** Unbind a toolkit from this agent (#607). See {@link useBindToolkitToAgent}
 * for the null-guard and cache-invalidation rationale. */
export function useUnbindToolkitFromAgent(agentId: string | null) {
	const invalidate = useInvalidateAgentBindingSurfaces(agentId);
	return useMutation<void, Error, string>({
		mutationFn: (toolkitId: string) => {
			if (!agentId) {
				return Promise.reject(new Error('Cannot unbind a toolkit before the agent loads.'));
			}
			return unbindToolkitFromAgent(agentId, toolkitId);
		},
		onSuccess: () => {
			invalidate();
			toast({ title: 'Toolkit unbound', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to unbind the toolkit.'),
	});
}

export function useAgentApiKeyInfo(id: string | null) {
	return useQuery<ApiKeyInfoEntity | null>({
		queryKey: agentsKeys.apiKeyInfo(id ?? ''),
		queryFn: () => getAgentApiKeyInfo(id as string),
		enabled: id != null,
	});
}

export function useAgentApiKeyHistory(id: string | null) {
	return useQuery<ApiKeyHistoryEntry[]>({
		queryKey: agentsKeys.apiKeyHistory(id ?? ''),
		queryFn: () => getAgentApiKeyHistory(id as string),
		enabled: id != null,
	});
}

// ---------------------------------------------------------------------------
// Agents — lifecycle mutations
// ---------------------------------------------------------------------------

export function useApproveAgent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => approveAgent(id),
		onSuccess: (agent) => {
			qc.setQueryData(agentsKeys.detail(agent.id), agent);
			qc.invalidateQueries({ queryKey: agentsKeys.lists() });
			// Approving removes the agent from the pending pool the Dashboard's
			// action inbox (`ActionInboxBell`) reads, and the persistent
			// nav badge (`usePendingAgentsCount`, keyed under the shared agents
			// root). Refresh both shared roots so those surfaces update instantly
			// instead of waiting for their fallback poll.
			qc.invalidateQueries({ queryKey: sharedQueryKeys.agentsRoot });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.dashboardRoot });
			toast({
				title: 'Agent approved',
				description: `${agent.name} is now active.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to approve the agent.'),
	});
}

export function useDenyAgent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, reason }: { id: string; reason: string }) => denyAgent(id, reason),
		onSuccess: (agent) => {
			qc.setQueryData(agentsKeys.detail(agent.id), agent);
			qc.invalidateQueries({ queryKey: agentsKeys.lists() });
			// Denying also clears the agent from the pending pool — keep the
			// Dashboard's pending-agents surfaces AND the nav badge in sync
			// immediately (both read off the shared agents root).
			qc.invalidateQueries({ queryKey: sharedQueryKeys.agentsRoot });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.dashboardRoot });
			toast({
				title: 'Agent denied',
				description: `${agent.name} was rejected.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to deny the agent.'),
	});
}

export function useDisableAgent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => disableAgent(id),
		onSuccess: (_void, id) => {
			qc.invalidateQueries({ queryKey: agentsKeys.lists() });
			qc.invalidateQueries({ queryKey: agentsKeys.detail(id) });
			toast({ title: 'Agent disabled', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to disable the agent.'),
	});
}

export function useEnableAgent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => enableAgent(id),
		onSuccess: (_void, id) => {
			qc.invalidateQueries({ queryKey: agentsKeys.lists() });
			qc.invalidateQueries({ queryKey: agentsKeys.detail(id) });
			toast({ title: 'Agent enabled', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to enable the agent.'),
	});
}

export function useArchiveAgent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => archiveAgent(id),
		onSuccess: (_void, id) => {
			qc.invalidateQueries({ queryKey: agentsKeys.lists() });
			qc.invalidateQueries({ queryKey: agentsKeys.detail(id) });
			toast({ title: 'Agent archived', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to archive the agent.'),
	});
}

export function useCreateAgent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (input: { name: string; description?: string | null; scopes?: string[] }) =>
			createAgent(input),
		onSuccess: (agent) => {
			// Invalidate the whole agents root (not just lists()) so the
			// persistent pending-agents nav badge — keyed under agentsRoot, not
			// under agentsKeys.lists() — refreshes immediately for a freshly
			// created (pending) agent rather than waiting for its fallback poll
			// (#652). The root prefix subsumes the list cache.
			qc.invalidateQueries({ queryKey: sharedQueryKeys.agentsRoot });
			// A freshly created agent starts in the pending pool, so refresh the
			// Dashboard's pending-agents surfaces too.
			qc.invalidateQueries({ queryKey: sharedQueryKeys.dashboardRoot });
			toast({
				title: 'Agent created',
				description: `${agent.name} created successfully.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to create the agent.'),
	});
}

/**
 * Partial in-place edit (PATCH /agents/{id}): rename, re-describe, or
 * reassign the owner from the detail page's Settings tab. Invalidates the
 * detail cache rather than seeding it from the PATCH response — the response
 * row is built without the `has_api_key` join (always false), so seeding it
 * would make the Keys tab forget an existing key. The roster refresh lets the
 * fleet table pick the new name up immediately, and the dashboard root covers
 * the pending-agents tile, which renders agent names.
 */
export function useUpdateAgent() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, patch }: { id: string; patch: AgentPatch }) => updateAgent(id, patch),
		onSuccess: (agent) => {
			qc.invalidateQueries({ queryKey: agentsKeys.detail(agent.id) });
			qc.invalidateQueries({ queryKey: agentsKeys.lists() });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.dashboardRoot });
			// A rename changes what every `ActorLabel` renders — monitor rows,
			// toolkit audit, access requests, and the "Registered by / Approved
			// by" grid on this very page all resolve names through the actor
			// directory (5-min staleTime, no focus refetch). Invalidate it so the
			// new name shows up immediately instead of after the staleTime.
			qc.invalidateQueries({ queryKey: sharedQueryKeys.actorDirectoryRoot });
			toast({
				title: 'Agent updated',
				description: `${agent.name} saved.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to update the agent.'),
	});
}

export function useGenerateAgentApiKey() {
	const qc = useQueryClient();
	return useMutation<ApiKeyResult, Error, string>({
		mutationFn: (agentId: string) => generateAgentApiKey(agentId),
		onSuccess: (_result, agentId) => {
			qc.invalidateQueries({ queryKey: agentsKeys.detail(agentId) });
			qc.invalidateQueries({ queryKey: agentsKeys.apiKeyInfo(agentId) });
			qc.invalidateQueries({ queryKey: agentsKeys.apiKeyHistory(agentId) });
		},
		onError: (e) => notifyError(e, 'Failed to generate API key.'),
	});
}

export function useRevokeAgentApiKey() {
	const qc = useQueryClient();
	return useMutation<void, Error, string>({
		mutationFn: (agentId: string) => revokeAgentApiKey(agentId),
		onSuccess: (_void, agentId) => {
			qc.invalidateQueries({ queryKey: agentsKeys.detail(agentId) });
			qc.invalidateQueries({ queryKey: agentsKeys.apiKeyInfo(agentId) });
			qc.invalidateQueries({ queryKey: agentsKeys.apiKeyHistory(agentId) });
			toast({ title: 'API key revoked', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to revoke API key.'),
	});
}

export function useGenerateServiceAccountApiKey() {
	return useMutation<ApiKeyResult, Error, string>({
		mutationFn: (serviceAccountId: string) => generateServiceAccountApiKey(serviceAccountId),
		onError: (e) => notifyError(e, 'Failed to generate API key.'),
	});
}

// ---------------------------------------------------------------------------
// Service accounts
// ---------------------------------------------------------------------------

/** Cursor-paginated service accounts — same infinite-query shape as
 * {@link useAgents} so the page renders both tabs with one component. */
export function useServiceAccounts(params: { status?: string } = {}) {
	const status = params.status ?? 'all';
	return useInfiniteQuery<ListResult<ServiceAccountEntity>>({
		queryKey: serviceAccountKeys.list(status),
		queryFn: ({ pageParam }) =>
			listServiceAccounts({
				status: status === 'all' ? null : status,
				cursor: (pageParam as string | null) ?? null,
			}),
		initialPageParam: null,
		getNextPageParam: (last) => (last.hasMore ? last.nextCursor : null),
		placeholderData: keepPreviousData,
	});
}

export function useServiceAccount(id: string | null) {
	return useQuery<ServiceAccountEntity>({
		queryKey: serviceAccountKeys.detail(id ?? ''),
		queryFn: () => getServiceAccount(id as string),
		enabled: id != null,
	});
}

export function useCreateServiceAccount() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (input: { name: string; description?: string | null; scopes?: string[] }) =>
			createServiceAccount(input),
		onSuccess: (sa) => {
			qc.invalidateQueries({ queryKey: serviceAccountKeys.lists() });
			// Unlike agents, service accounts are approved at creation (the
			// backend calls set_approval inside the create transaction).
			toast({
				title: 'Service account created',
				description: `${sa.name} is ready to use.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to create the service account.'),
	});
}

export function useApproveServiceAccount() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => approveServiceAccount(id),
		onSuccess: (sa) => {
			qc.setQueryData(serviceAccountKeys.detail(sa.id), sa);
			qc.invalidateQueries({ queryKey: serviceAccountKeys.lists() });
			toast({ title: 'Service account approved', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to approve the service account.'),
	});
}

export function useDenyServiceAccount() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, reason }: { id: string; reason: string }) =>
			denyServiceAccount(id, reason),
		onSuccess: (sa) => {
			qc.setQueryData(serviceAccountKeys.detail(sa.id), sa);
			qc.invalidateQueries({ queryKey: serviceAccountKeys.lists() });
			toast({ title: 'Service account denied', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to deny the service account.'),
	});
}

export function useDisableServiceAccount() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => disableServiceAccount(id),
		onSuccess: (_void, id) => {
			qc.invalidateQueries({ queryKey: serviceAccountKeys.lists() });
			qc.invalidateQueries({ queryKey: serviceAccountKeys.detail(id) });
			toast({ title: 'Service account disabled', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to disable the service account.'),
	});
}

export function useEnableServiceAccount() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => enableServiceAccount(id),
		onSuccess: (_void, id) => {
			qc.invalidateQueries({ queryKey: serviceAccountKeys.lists() });
			qc.invalidateQueries({ queryKey: serviceAccountKeys.detail(id) });
			toast({ title: 'Service account enabled', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to enable the service account.'),
	});
}

export function useArchiveServiceAccount() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => archiveServiceAccount(id),
		onSuccess: (_void, id) => {
			qc.invalidateQueries({ queryKey: serviceAccountKeys.lists() });
			qc.invalidateQueries({ queryKey: serviceAccountKeys.detail(id) });
			toast({ title: 'Service account archived', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to archive the service account.'),
	});
}

// ---------------------------------------------------------------------------
// Scopes (#615)
// ---------------------------------------------------------------------------

/**
 * The platform permission catalogue. Small + slow-changing, so it's cached
 * generously; the Scopes editor maps it into the picker's scope list and uses
 * `grantableByCaller` to disable scopes the operator can't grant.
 */
export function usePermissionCatalogue(options: { enabled?: boolean } = {}) {
	return useQuery<PermissionCatalogEntry[]>({
		queryKey: permissionsKey,
		queryFn: () => listPermissions(),
		staleTime: 5 * 60 * 1000,
		enabled: options.enabled ?? true,
	});
}

export function useAgentScopes(id: string | null) {
	return useQuery<string[]>({
		queryKey: agentsKeys.scopes(id ?? ''),
		queryFn: () => getAgentScopes(id as string),
		enabled: id != null,
	});
}

export function useReplaceAgentScopes() {
	const qc = useQueryClient();
	return useMutation<string[], Error, { id: string; scopes: string[] }>({
		mutationFn: ({ id, scopes }) => replaceAgentScopes(id, scopes),
		onSuccess: (scopes, { id }) => {
			qc.setQueryData(agentsKeys.scopes(id), scopes);
			toast({ title: 'Scopes updated', variant: 'success' });
		},
		onError: (e) => notifyError(e, "Failed to update the agent's scopes."),
	});
}

export function useServiceAccountScopes(id: string | null) {
	return useQuery<string[]>({
		queryKey: serviceAccountKeys.scopes(id ?? ''),
		queryFn: () => getServiceAccountScopes(id as string),
		enabled: id != null,
	});
}

export function useReplaceServiceAccountScopes() {
	const qc = useQueryClient();
	return useMutation<string[], Error, { id: string; scopes: string[] }>({
		mutationFn: ({ id, scopes }) => replaceServiceAccountScopes(id, scopes),
		onSuccess: (scopes, { id }) => {
			qc.setQueryData(serviceAccountKeys.scopes(id), scopes);
			toast({ title: 'Scopes updated', variant: 'success' });
		},
		onError: (e) => notifyError(e, "Failed to update the service account's scopes."),
	});
}

/**
 * Per-actor execution stats for the fleet table's activity columns
 * (`GET /monitoring/usage?group_by=agent`, trailing 7 days). Kept under its
 * OWN root (like `linkableToolkitsKey`) so agent lifecycle invalidations —
 * which sweep `sharedQueryKeys.agentsRoot` on approve/deny/create — don't
 * pointlessly re-aggregate the monitoring window. Resolves `null` for
 * non-admins (403): the table renders without activity columns rather than
 * erroring, and `retry: false` stops TanStack from hammering a gate that
 * won't open. Any other failure also degrades to no columns (no toast — the
 * roster itself is the page's primary data, usage is enrichment).
 */
export function useActorsUsage(actorType: 'agent' | 'service_account') {
	return useQuery<Map<string, ActorUsage> | null>({
		queryKey: ['agents-usage', actorType],
		queryFn: () => fetchActorsUsage(actorType),
		staleTime: 60 * 1000,
		retry: false,
	});
}

/**
 * One actor's usage stats + volume buckets (trailing 7 days) for the detail
 * page's KPI strip and Activity chart. Same `agents-usage` root and 403/`null`
 * degrade contract as `useActorsUsage` (see its docblock).
 */
export function useActorUsageDetail(actorId: string | null) {
	return useQuery<ActorUsageDetail | null>({
		queryKey: ['agents-usage', 'detail', actorId],
		queryFn: () => fetchActorUsageDetail(actorId as string),
		enabled: actorId != null,
		// Matches useActorExecutions below: the KPI/volume chart and the
		// recent-executions feed render side by side and must go stale
		// together, or the feed refreshes ahead of the chart and the two
		// disagree for up to 30s (#913).
		staleTime: 30 * 1000,
		retry: false,
	});
}

/**
 * The most recent executions attributed to one actor — the detail page's
 * Activity feed. Single page by design: the full, filterable history lives in
 * Monitor (the feed carries a pre-filtered deep-link). `null` on 403.
 */
export function useActorExecutions(actorId: string | null) {
	return useQuery<{ items: ActorExecutionEntity[]; hasMore: boolean } | null>({
		queryKey: ['agents-usage', 'executions', actorId],
		queryFn: () => fetchActorExecutions(actorId as string),
		enabled: actorId != null,
		staleTime: 30 * 1000,
		retry: false,
	});
}

/**
 * Access requests filed by a single actor (`GET /access-requests?actor_id=…`),
 * defaulting to the still-pending queue (#619). Works for both agents and
 * service accounts — the backend keys requests by `actor_id`, which is the
 * actor's own id. Pass `status: null` to fetch every status (the "All" filter).
 * `enabled` only when an id is present so the detail page's loading/not-found
 * states aren't disturbed.
 */
export function useActorAccessRequests(actorId: string | null, status: string | null = 'pending') {
	return useQuery<AccessRequest[]>({
		queryKey: actorAccessRequestsKey(actorId ?? '', status ?? 'all'),
		queryFn: () => fetchActorAccessRequests(actorId as string, status),
		enabled: actorId != null,
	});
}

/**
 * Actor-scoped audit trail for the detail console's "Recent changes" panel —
 * the lifecycle events recorded against this agent / service account as the
 * TARGET. Mirrors the toolkit console's `useToolkitAudit`. Non-admins resolve
 * to an empty list (the client maps 401/403), so the panel renders its
 * graceful "no entries" state instead of erroring.
 */
export function useActorAudit(actorKind: 'agent' | 'service-account', actorId: string | null) {
	return useQuery<ActorAuditEntry[]>({
		queryKey: ['agents', 'audit', actorKind, actorId],
		queryFn: () => listActorAudit(actorKind, actorId as string),
		enabled: actorId != null,
		staleTime: 30 * 1000,
	});
}
