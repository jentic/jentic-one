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
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	approveAgent,
	approveServiceAccount,
	archiveAgent,
	archiveServiceAccount,
	createAgent,
	createServiceAccount,
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
	listAgentToolkits,
	listAgents,
	listPermissions,
	listServiceAccounts,
	replaceAgentScopes,
	replaceServiceAccountScopes,
	revokeAgentApiKey,
	fetchActorAccessRequests,
	type ListResult,
} from '@/modules/agents/api/client';
import type {
	AgentEntity,
	ApiKeyHistoryEntry,
	ApiKeyInfoEntity,
	ApiKeyResult,
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

export function useAgents(params: { status?: string }) {
	const status = params.status ?? 'all';
	return useQuery<ListResult<AgentEntity>>({
		queryKey: agentsKeys.list(status),
		queryFn: () => listAgents({ status: status === 'all' ? null : status }),
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
			// "Awaiting approval" tile + PendingAgentsCard read, and the persistent
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
		mutationFn: (input: { name: string; description?: string | null }) => createAgent(input),
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

export function useServiceAccounts(params: { status?: string }) {
	const status = params.status ?? 'all';
	return useQuery<ListResult<ServiceAccountEntity>>({
		queryKey: serviceAccountKeys.list(status),
		queryFn: () => listServiceAccounts({ status: status === 'all' ? null : status }),
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
		mutationFn: (input: { name: string; description?: string | null }) =>
			createServiceAccount(input),
		onSuccess: (sa) => {
			qc.invalidateQueries({ queryKey: serviceAccountKeys.lists() });
			toast({
				title: 'Service account created',
				description: `${sa.name} is pending approval.`,
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
export function usePermissionCatalogue() {
	return useQuery<PermissionCatalogEntry[]>({
		queryKey: permissionsKey,
		queryFn: () => listPermissions(),
		staleTime: 5 * 60 * 1000,
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
