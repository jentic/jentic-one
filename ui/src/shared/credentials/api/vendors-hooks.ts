/**
 * Integrations service tier — TanStack Query hooks over the repository.
 *
 * Views (pages/components) call ONLY these hooks; direct fetch calls or
 * `@/shared/api` imports from views are forbidden by ESLint.
 */

import {
	useMutation,
	useQueries,
	useQuery,
	useQueryClient,
	type QueryClient,
	type UseQueryOptions,
} from '@tanstack/react-query';
import { useCallback } from 'react';
import {
	bindCredentialToAgentBlocked,
	listBindingPermissions,
	replaceBindingPermissions,
	cancelConnectSession,
	confirmConnectSession,
	getConnectSession,
	getVendorAuthCapabilities,
	listAgentsForPicker,
	listAllVendorOperations,
	listOpenConnectRequests,
	listVendors,
	pollConnectSessionStatus,
	startIntegrationConnect,
	type VendorOperationsPage,
} from '@/shared/credentials/api/vendors-client';
import type {
	AgentListResponse,
	ConnectSessionSummaryResponse,
	PermissionRuleReadSchema,
	PermissionRuleSchema,
} from '@/shared/api';
import { sharedQueryKeys } from '@/shared/api/queryKeys';
import { credentialKeys } from './keys';
import type {
	ConfirmRequest,
	ConfirmResponse,
	ConnectRequest,
	ConnectResponse,
	ReviewSession,
	StatusResponse,
	VendorAuthCapabilities,
	VendorListResponse,
} from '@/shared/credentials/api/vendors-types';

const KEYS = {
	// A token-less (owner / admin) read keys on `''`, apart from any token holder's.
	session: (id: string, token: string) => ['integrations', 'session', id, token] as const,
	status: (id: string, token: string) => ['integrations', 'status', id, token] as const,
	vendors: ['integrations', 'vendors'] as const,
	vendor: (key: string, registrationId?: string | null) =>
		// Include the pinned registration id in the cache key so switching
		// between two admin-registered OAuth apps for the same vendor slug
		// misses the cache instead of showing the previous tile's scopes.
		['integrations', 'vendor', key, registrationId ?? null] as const,
} as const;

export function useVendors() {
	return useQuery<VendorListResponse>({
		queryKey: KEYS.vendors,
		queryFn: listVendors,
		staleTime: 60_000,
	});
}

export function useVendorAuthCapabilities(
	vendorKey: string | undefined,
	registrationId?: string | null,
) {
	return useQuery<VendorAuthCapabilities>({
		queryKey: KEYS.vendor(vendorKey ?? '', registrationId ?? null),
		queryFn: () => getVendorAuthCapabilities(vendorKey as string, registrationId ?? null),
		enabled: Boolean(vendorKey),
		staleTime: 60_000,
	});
}

export function useAgentsForPicker() {
	return useQuery<AgentListResponse>({
		queryKey: ['integrations', 'agents-picker'],
		queryFn: listAgentsForPicker,
		staleTime: 30_000,
	});
}

/**
 * Review data for a connect session. `pollToken` is optional: without it the
 * read succeeds only for the target agent's owner or `org:admin`.
 */
export function useConnectSession(
	sessionId: string | undefined,
	pollToken: string | undefined,
	options?: Partial<UseQueryOptions<ReviewSession>>,
) {
	return useQuery<ReviewSession>({
		queryKey: KEYS.session(sessionId ?? '', pollToken ?? ''),
		queryFn: () => getConnectSession(sessionId as string, pollToken),
		enabled: Boolean(sessionId),
		staleTime: 5_000,
		// A 403 means the poll_token doesn't match, the session is gone, or
		// a token-less caller isn't the owner / admin (the backend
		// deliberately conflates them — no enumeration oracle). Retrying
		// can't fix any of them, so fail fast; the caller surfaces "the
		// approval link is no longer valid".
		retry: (failureCount, error) => {
			const status = (error as { status?: number | null })?.status;
			if (typeof status === 'number' && status >= 400 && status < 500) return false;
			return failureCount < 2;
		},
		// Poll while ``api_reference.version`` is null — the catalog import
		// runs asynchronously, so the value flips from null to a version
		// string once the import job completes. Once populated, stop
		// polling so we don't hammer the endpoint for no reason. Also stop
		// on 4xx errors (403 = token mismatch / session gone — terminal).
		// Callers that override the query behaviour can still pass their
		// own ``refetchInterval`` via ``options``.
		refetchInterval: (query) => {
			const status = (query.state.error as { status?: number | null } | null)?.status;
			if (typeof status === 'number' && status >= 400 && status < 500) return false;
			const data = query.state.data;
			if (data?.api_reference?.version) return false;
			return 2_000;
		},
		...options,
	});
}

export function useConfirmConnectSession(sessionId: string, pollToken: string | undefined) {
	return useMutation<ConfirmResponse, Error, ConfirmRequest>({
		mutationFn: (body) => confirmConnectSession(sessionId, pollToken, body),
	});
}

/**
 * Fetch every operation for a vendor's imported OpenAPI. Follows
 * ``next_cursor`` server-side pagination until the full list is loaded
 * (via ``listAllVendorOperations``) so the rules-page preview,
 * autocomplete, and grouper all see every operation — a single 50/200-
 * op page would leave large vendors like GitHub with most of their
 * surface hidden. Polls every 2s while ``data`` is ``null`` (import
 * still queued — the client returns null on 404). ``enabled`` gates
 * the whole thing to when the caller actually needs it (e.g. only on
 * the rules phase).
 */
export function useVendorOperations(
	api: { vendor: string; name: string | null; version: string | null } | null | undefined,
	opts: { enabled?: boolean } = {},
) {
	const ready = !!api && !!api.name && !!api.version;
	return useQuery<VendorOperationsPage | null>({
		queryKey: [
			'integrations',
			'operations',
			api?.vendor ?? '',
			api?.name ?? '',
			api?.version ?? '',
			'all',
		] as const,
		queryFn: () =>
			listAllVendorOperations(api!.vendor, api!.name as string, api!.version as string),
		enabled: (opts.enabled ?? true) && ready,
		// Poll while ``data`` is null (import still queued). Once ops land,
		// stop polling.
		refetchInterval: (query) => (query.state.data == null ? 2_000 : false),
		staleTime: 30_000,
	});
}

export function useStartIntegrationConnect() {
	return useMutation<ConnectResponse, Error, ConnectRequest>({
		mutationFn: (body) => startIntegrationConnect(body),
	});
}

/**
 * Bind a credential to a batch of agents in "start blocked" mode (no
 * rules). Powers the post-connect "Bind to more agents" CTA: after
 * the user finishes the OAuth flow, they can tick a set of other
 * agents that should also have access to the credential, and click
 * Bind. The per-binding rules the user authored at ``:confirm`` were
 * specific to the primary (agent, credential) pair — additional
 * agents get suspended rows that the user grants access from each
 * agent's page.
 *
 * Serial rather than parallel: a 409 (already-bound out-of-band) on
 * one agent shouldn't cancel the others' in-flight requests. The
 * mutation invalidates each touched agent's binding list and the
 * credential's bindings list so the surrounding UI refreshes without
 * a manual reload.
 */
/**
 * The agent-side caches a binding change touches, as literals mirroring
 * `agentsKeys` (shared code can't import the agents module): each agent's
 * binding list and its rules on this credential, plus the credential's agent
 * roster — the slice the API hub's "Who can use it" and the Library's
 * workspace agent counts read (the prefix also sweeps its all-pages variant).
 */
export function invalidateBindingSurfaces(
	client: QueryClient,
	credentialId: string,
	agentIds: readonly string[],
): void {
	for (const aid of agentIds) {
		void client.invalidateQueries({
			queryKey: [...sharedQueryKeys.agentsRoot, 'credential-bindings', aid],
		});
		void client.invalidateQueries({
			queryKey: bindingPermissionsKey(aid, credentialId),
		});
	}
	void client.invalidateQueries({ queryKey: credentialKeys.agents(credentialId) });
}

/** Mirrors `agentsKeys.bindingPermissions` — the agent rules editor reads the same slice. */
function bindingPermissionsKey(agentId: string, credentialId: string) {
	return [...sharedQueryKeys.agentsRoot, 'binding-permissions', agentId, credentialId] as const;
}

export interface BindResult {
	/** Agents bound whose rules couldn't be saved — bound, but blocked until they are. */
	rulesFailed: string[];
}

/**
 * Bind a credential to each agent (`POST /agents/{id}/credentials`, created
 * blocked), then — when `rules` is given — replace that binding's rules
 * (`PUT …/permissions`), agent by agent, so each one is either fully granted
 * or reported. A failed bind stops the run (agents before it stay bound); a
 * failed rules save doesn't — it lands in `rulesFailed` for a retry
 * ({@link useApplyBindingRules}). Caches are invalidated whatever the outcome,
 * so a run that fails partway still shows the binds that landed.
 */
export function useBindCredentialToAgents() {
	const client = useQueryClient();
	return useMutation<
		BindResult,
		Error,
		{
			credentialId: string;
			agentIds: readonly string[];
			rules?: readonly PermissionRuleSchema[];
		}
	>({
		mutationFn: async ({ credentialId, agentIds, rules }) => {
			const rulesFailed: string[] = [];
			for (const aid of agentIds) {
				await bindCredentialToAgentBlocked(aid, credentialId);
				if (!rules) continue;
				try {
					await replaceBindingPermissions(aid, credentialId, [...rules]);
				} catch {
					rulesFailed.push(aid);
				}
			}
			return { rulesFailed };
		},
		onSettled: (_res, _err, { credentialId, agentIds }) =>
			invalidateBindingSurfaces(client, credentialId, agentIds),
	});
}

/** Save `rules` on existing bindings (the Retry after a bind's rules failed). */
export function useApplyBindingRules() {
	const client = useQueryClient();
	return useMutation<
		BindResult,
		Error,
		{
			credentialId: string;
			agentIds: readonly string[];
			rules: readonly PermissionRuleSchema[];
		}
	>({
		mutationFn: async ({ credentialId, agentIds, rules }) => {
			const rulesFailed: string[] = [];
			for (const aid of agentIds) {
				try {
					await replaceBindingPermissions(aid, credentialId, [...rules]);
				} catch {
					rulesFailed.push(aid);
				}
			}
			return { rulesFailed };
		},
		onSettled: (_res, _err, { credentialId, agentIds }) =>
			invalidateBindingSurfaces(client, credentialId, agentIds),
	});
}

/** Where one binding's access stands, from its saved operator rules. */
export type BindingAccessState = 'loading' | 'unknown' | 'blocked' | 'open';

/** No operator `allow` rule ⇒ the broker denies every call (default deny). */
function accessFromRules(rules: readonly PermissionRuleReadSchema[]): 'blocked' | 'open' {
	return rules.some((r) => !r._system && String(r.effect) === 'allow') ? 'open' : 'blocked';
}

/**
 * The access state of each (agent, credential) binding, keyed
 * `${agentId}\n${credentialId}` — read from the binding's rules in the same
 * cache slice the agent's rules editor uses, so a save there shows here.
 */
export function useBindingAccessStates(
	pairs: ReadonlyArray<{ agentId: string; credentialId: string }>,
): ReadonlyMap<string, BindingAccessState> {
	const combine = useCallback(
		(results: { data?: PermissionRuleReadSchema[]; isError: boolean }[]) => {
			const map = new Map<string, BindingAccessState>();
			results.forEach((r, i) => {
				const pair = pairs[i];
				if (!pair) return;
				map.set(
					`${pair.agentId}\n${pair.credentialId}`,
					r.data ? accessFromRules(r.data) : r.isError ? 'unknown' : 'loading',
				);
			});
			return map;
		},
		[pairs],
	);
	return useQueries({
		queries: pairs.map(({ agentId, credentialId }) => ({
			queryKey: bindingPermissionsKey(agentId, credentialId),
			queryFn: () => listBindingPermissions(agentId, credentialId),
		})),
		combine,
	});
}

/**
 * Cancel an in-flight connect session (Cancel button, dialog dismiss,
 * unmount cleanup while phase != terminal). Idempotent by construction
 * — the ``:cancel`` route no-ops on already-terminal sessions — so
 * callers can fire this without state-guarding it themselves.
 */
export function useCancelConnectSession() {
	const client = useQueryClient();
	return useMutation<void, Error, { sessionId: string; pollToken?: string }>({
		mutationFn: ({ sessionId, pollToken }) => cancelConnectSession(sessionId, pollToken),
		onSuccess: () => {
			// The credential + session are cascade-deleted on the backend;
			// invalidate so the credentials list and the open-request
			// signals drop the stale rows.
			void client.invalidateQueries({ queryKey: ['credentials'] });
			void client.invalidateQueries({ queryKey: connectRequestsKey });
		},
	});
}

/**
 * The open-request list's key. Under the attention root, so anything that
 * refreshes the inbox (an approval decided elsewhere) refreshes it too.
 */
export const connectRequestsKey = [...sharedQueryKeys.attentionRoot, 'connect-requests'] as const;

/** Same cadence as the rest of the inbox: roughly live without a push channel. */
const CONNECT_REQUESTS_REFETCH_MS = 45_000;

/**
 * Connect sessions an agent opened and is still waiting on a human for
 * (`created` / `polling`), oldest first — the live state behind the
 * attention inbox's "waiting for you" rows and the Agents page section.
 * Pass `enabled: false` for a caller who cannot read credentials (the list
 * needs `credentials:read` or `owner:credentials:read`).
 */
export function useOpenConnectRequests(options?: { enabled?: boolean }) {
	return useQuery<ConnectSessionSummaryResponse[]>({
		queryKey: connectRequestsKey,
		queryFn: listOpenConnectRequests,
		enabled: options?.enabled ?? true,
		staleTime: 30_000,
		refetchInterval: CONNECT_REQUESTS_REFETCH_MS,
	});
}

/**
 * Poll the vendor status. Callers set `enabled` to control when polling runs;
 * TanStack Query's `refetchInterval` drives the cadence. On terminal states
 * (connected / failed / expired) the caller should flip `enabled=false`.
 */
export function usePollConnectSessionStatus(
	sessionId: string,
	pollToken: string | undefined,
	options?: {
		enabled?: boolean;
		intervalMs?: number;
	},
) {
	return useQuery<StatusResponse>({
		queryKey: KEYS.status(sessionId, pollToken ?? ''),
		queryFn: () => pollConnectSessionStatus(sessionId, pollToken),
		enabled: options?.enabled ?? true,
		// Default matches RFC 8628 §3.5's device-flow ``interval`` fallback
		// (5s) so a caller that forgets to thread ``poll_interval_seconds``
		// from the challenge still hits the spec-correct cadence rather
		// than an out-of-spec 3s hammer. All wired-up callers should
		// override via ``intervalMs``.
		refetchInterval: options?.enabled === false ? false : (options?.intervalMs ?? 5000),
		refetchIntervalInBackground: true,
		staleTime: 0,
		gcTime: 0,
	});
}
