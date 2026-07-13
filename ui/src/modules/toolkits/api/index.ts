/**
 * Toolkits service tier — TanStack Query hooks (≈ backend Service layer).
 *
 * The ONLY backend access path for Toolkits views: components/pages call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views must never reach past this layer (ESLint-enforced). Query keys are
 * namespaced under `['toolkits', …]` so callers/tests can target invalidation.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import type {
	ToolkitCreateRequest,
	ToolkitUpdateRequest,
	ToolkitKeyCreateRequest,
	ToolkitKeyUpdateRequest,
	ToolkitCredentialBindRequest,
	PermissionRuleSchema,
} from '@/shared/api';
import * as client from '@/modules/toolkits/api/client';
import type { CreatedToolkit } from '@/modules/toolkits/api/types';

/** Stable query-key roots so callers/tests can target invalidation precisely. */
export const toolkitKeys = {
	all: ['toolkits'] as const,
	list: (cursor: string | null) => [...toolkitKeys.all, 'list', cursor] as const,
	detail: (id: string) => [...toolkitKeys.all, 'detail', id] as const,
	keys: (id: string) => [...toolkitKeys.all, 'keys', id] as const,
	bindings: (id: string) => [...toolkitKeys.all, 'bindings', id] as const,
	permissions: (id: string, credentialId: string) =>
		[...toolkitKeys.all, 'permissions', id, credentialId] as const,
	agents: (id: string) => [...toolkitKeys.all, 'agents', id] as const,
	audit: (id: string) => [...toolkitKeys.all, 'audit', id] as const,
	// Toolkit-scoped lists not tied to a single toolkit id.
	bindableCredentials: () => [...toolkitKeys.all, 'bindable-credentials'] as const,
	linkableAgents: () => [...toolkitKeys.all, 'linkable-agents'] as const,
	agentBindings: (agentId: string) => [...toolkitKeys.all, 'agent-bindings', agentId] as const,
};

const STALE_POLL_MS = 30_000;

// --- Toolkit list + detail ------------------------------------------------

export function useToolkits(params: { cursor?: string | null } = {}) {
	const cursor = params.cursor ?? null;
	return useQuery({
		queryKey: toolkitKeys.list(cursor),
		queryFn: () => client.listToolkits({ cursor }),
		placeholderData: keepPreviousData,
		refetchInterval: STALE_POLL_MS,
	});
}

export function useToolkit(toolkitId: string | null, opts: { poll?: boolean } = {}) {
	return useQuery({
		queryKey: toolkitKeys.detail(toolkitId ?? ''),
		queryFn: () => client.getToolkit(toolkitId as string),
		enabled: toolkitId != null,
		refetchInterval: opts.poll === false ? false : STALE_POLL_MS,
	});
}

// --- Toolkit mutations ----------------------------------------------------

export function useCreateToolkit() {
	const queryClient = useQueryClient();
	return useMutation<CreatedToolkit, Error, ToolkitCreateRequest>({
		mutationFn: (body) => client.createToolkit(body),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.all });
		},
	});
}

export function useUpdateToolkit(toolkitId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (body: ToolkitUpdateRequest) => client.updateToolkit(toolkitId, body),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.detail(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.all });
		},
	});
}

/** Suspend / restore via the `active` flag — the kill switch. */
export function useSetToolkitActive(toolkitId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (active: boolean) => client.setToolkitActive(toolkitId, active),
		onSuccess: (toolkit) => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.detail(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.all });
			toast({
				title: toolkit.active ? 'Toolkit restored' : 'Toolkit suspended',
				variant: toolkit.active ? 'success' : 'default',
			});
		},
		onError: (err: Error) =>
			toast({
				title: 'Failed to change toolkit status',
				description: err.message,
				variant: 'error',
			}),
	});
}

/**
 * Hard-delete a toolkit. Irreversible and cascades to keys, bindings, and
 * permission rules — gate this behind `CascadeDeleteDialog`. The kill switch
 * (`useSetToolkitActive`) remains the reversible option.
 */
export function useDeleteToolkit() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (toolkitId: string) => client.deleteToolkit(toolkitId),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.all });
			toast({ title: 'Toolkit deleted', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({
				title: 'Failed to delete toolkit',
				description: err.message,
				variant: 'error',
			}),
	});
}

// --- Keys -----------------------------------------------------------------

export function useToolkitKeys(toolkitId: string | null, opts: { poll?: boolean } = {}) {
	return useQuery({
		queryKey: toolkitKeys.keys(toolkitId ?? ''),
		queryFn: () => client.listKeys(toolkitId as string),
		enabled: toolkitId != null,
		select: (res) => res.data,
		refetchInterval: opts.poll === false ? false : STALE_POLL_MS,
	});
}

export function useCreateKey(toolkitId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (body: ToolkitKeyCreateRequest) => client.createKey(toolkitId, body),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.keys(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.detail(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.all });
		},
	});
}

export function useRevokeKey(toolkitId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (keyId: string) =>
			client.updateKey(toolkitId, keyId, { revoked: true } satisfies ToolkitKeyUpdateRequest),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.keys(toolkitId) });
			toast({ title: 'API key revoked', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({ title: 'Failed to revoke key', description: err.message, variant: 'error' }),
	});
}

export function useDeleteKey(toolkitId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (keyId: string) => client.deleteKey(toolkitId, keyId),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.keys(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.detail(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.all });
			toast({ title: 'API key deleted', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({ title: 'Failed to delete key', description: err.message, variant: 'error' }),
	});
}

// --- Credential bindings --------------------------------------------------

export function useToolkitBindings(toolkitId: string | null, opts: { poll?: boolean } = {}) {
	return useQuery({
		queryKey: toolkitKeys.bindings(toolkitId ?? ''),
		queryFn: () => client.listBindings(toolkitId as string),
		enabled: toolkitId != null,
		select: (res) => res.data,
		refetchInterval: opts.poll === false ? false : STALE_POLL_MS,
	});
}

export function useBindCredential(toolkitId: string) {
	const invalidate = useInvalidateToolkitSurfaces(toolkitId);
	return useMutation({
		mutationFn: (body: ToolkitCredentialBindRequest) => client.bindCredential(toolkitId, body),
		onSuccess: () => {
			invalidate();
			toast({ title: 'Credential bound', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({
				title: 'Failed to bind credential',
				description: err.message,
				variant: 'error',
			}),
	});
}

export function useUnbindCredential(toolkitId: string) {
	const invalidate = useInvalidateToolkitSurfaces(toolkitId);
	return useMutation({
		mutationFn: (credentialId: string) => client.unbindCredential(toolkitId, credentialId),
		onSuccess: () => {
			invalidate();
			toast({ title: 'Credential unbound', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({
				title: 'Failed to unbind credential',
				description: err.message,
				variant: 'error',
			}),
	});
}

/**
 * Workspace credentials available to bind to a toolkit — powers the bind
 * dialog's searchable picker. Independent of any single toolkit, so it's keyed
 * outside the per-toolkit namespace and shared across toolkits.
 */
export function useBindableCredentials(opts: { enabled?: boolean } = {}) {
	return useQuery({
		queryKey: toolkitKeys.bindableCredentials(),
		queryFn: () => client.listBindableCredentials(),
		enabled: opts.enabled ?? true,
		staleTime: 15_000,
	});
}

// --- Per-binding permission rules ----------------------------------------

export function useToolkitPermissions(toolkitId: string, credentialId: string | null) {
	return useQuery({
		queryKey: toolkitKeys.permissions(toolkitId, credentialId ?? ''),
		queryFn: () => client.listPermissions(toolkitId, credentialId as string),
		enabled: credentialId != null,
		select: (res) => res.data,
	});
}

export function useReplacePermissions(toolkitId: string, credentialId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (rules: PermissionRuleSchema[]) =>
			client.replacePermissions(toolkitId, credentialId, rules),
		onSuccess: () => {
			queryClient.invalidateQueries({
				queryKey: toolkitKeys.permissions(toolkitId, credentialId),
			});
			queryClient.invalidateQueries({ queryKey: toolkitKeys.bindings(toolkitId) });
			toast({ title: 'Permission rules saved', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({ title: 'Failed to save rules', description: err.message, variant: 'error' }),
	});
}

// --- Agent bindings (agent side) -----------------------------------------

/**
 * Agents bound to a toolkit — the reverse lookup powering the detail page's
 * "Bound Agents" section. Served by `GET /toolkits/{id}/agents`.
 */
export function useToolkitAgents(toolkitId: string | null, opts: { poll?: boolean } = {}) {
	return useQuery({
		queryKey: toolkitKeys.agents(toolkitId ?? ''),
		queryFn: () => client.listToolkitAgents(toolkitId as string),
		enabled: toolkitId != null,
		refetchInterval: opts.poll === false ? false : STALE_POLL_MS,
	});
}

/** All workspace agents — the candidate list for the "Link agent" picker. */
export function useLinkableAgents(opts: { enabled?: boolean } = {}) {
	return useQuery({
		queryKey: toolkitKeys.linkableAgents(),
		queryFn: () => client.listLinkableAgents(),
		enabled: opts.enabled ?? true,
		staleTime: 15_000,
	});
}

/** Grant a toolkit to an agent (binds toolkit → agent on the /agents side). */
export function useLinkAgentToToolkit(toolkitId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (agentId: string) => client.bindToolkitToAgent(agentId, toolkitId),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.agents(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.detail(toolkitId) });
			// The link picker's candidate list is keyed outside the per-toolkit
			// namespace; refresh it too so reopening the dialog reflects the change.
			queryClient.invalidateQueries({ queryKey: toolkitKeys.linkableAgents() });
			toast({ title: 'Agent linked', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({ title: 'Failed to link agent', description: err.message, variant: 'error' }),
	});
}

export function useAgentToolkits(agentId: string | null) {
	return useQuery({
		queryKey: toolkitKeys.agentBindings(agentId ?? ''),
		queryFn: () => client.listAgentToolkits(agentId as string),
		enabled: agentId != null,
	});
}

export function useUnbindToolkitFromAgent(toolkitId: string) {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (agentId: string) => client.unbindToolkitFromAgent(agentId, toolkitId),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: toolkitKeys.agents(toolkitId) });
			queryClient.invalidateQueries({ queryKey: toolkitKeys.detail(toolkitId) });
			// Unlinked agents become linkable again — refresh the picker candidates.
			queryClient.invalidateQueries({ queryKey: toolkitKeys.linkableAgents() });
			toast({ title: 'Agent access revoked', variant: 'success' });
		},
		onError: (err: Error) =>
			toast({ title: 'Failed to revoke agent', description: err.message, variant: 'error' }),
	});
}

// --- Audit (read-only toolkit-scoped lens) --------------------------------

export function useToolkitAudit(toolkitId: string | null, opts: { poll?: boolean } = {}) {
	return useQuery({
		queryKey: toolkitKeys.audit(toolkitId ?? ''),
		queryFn: () => client.listToolkitAudit(toolkitId as string),
		enabled: toolkitId != null,
		refetchInterval: opts.poll === false ? false : STALE_POLL_MS,
	});
}

// --- Shared invalidation helper -------------------------------------------
/**
 * Bind/unbind/permission changes ripple across surfaces that host the toolkit
 * (detail, list, the credential-count rollups). Invalidate them together so
 * counts/lists never go stale. Mirrors mini's `invalidateToolkitSurfaces`.
 */
function useInvalidateToolkitSurfaces(toolkitId: string) {
	const queryClient = useQueryClient();
	return () => {
		queryClient.invalidateQueries({ queryKey: toolkitKeys.detail(toolkitId) });
		queryClient.invalidateQueries({ queryKey: toolkitKeys.bindings(toolkitId) });
		queryClient.invalidateQueries({ queryKey: toolkitKeys.all });
		// The bind picker's candidate list lives outside the per-toolkit namespace;
		// refresh it so reopening the dialog reflects newly bound/unbound creds.
		queryClient.invalidateQueries({ queryKey: toolkitKeys.bindableCredentials() });
	};
}

export { ToolkitsApiError } from '@/modules/toolkits/api/client';
