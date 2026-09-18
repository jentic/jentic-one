/**
 * Integrations service tier — TanStack Query hooks over the repository.
 *
 * Views (pages/components) call ONLY these hooks; direct fetch calls or
 * `@/shared/api` imports from views are forbidden by ESLint.
 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query';
import {
	bindCredentialToAgentBlocked,
	cancelConnectSession,
	confirmConnectSession,
	getConnectSession,
	getVendorAuthCapabilities,
	listAgentsForPicker,
	listAllVendorOperations,
	listVendors,
	pollConnectSessionStatus,
	startIntegrationConnect,
	type VendorOperationsPage,
} from '@/shared/credentials/api/vendors-client';
import type { AgentListResponse } from '@/shared/api';
import type {
	ConfirmRequest,
	ConfirmResponse,
	ConnectRequest,
	ConnectResponse,
	PermissionRule,
	ReviewSession,
	StatusResponse,
	VendorAuthCapabilities,
	VendorListResponse,
} from '@/shared/credentials/api/vendors-types';

const KEYS = {
	session: (id: string, token: string) => ['integrations', 'session', id, token] as const,
	status: (id: string, token: string) => ['integrations', 'status', id, token] as const,
	vendors: ['integrations', 'vendors'] as const,
	vendor: (key: string) => ['integrations', 'vendor', key] as const,
} as const;

export function useVendors() {
	return useQuery<VendorListResponse>({
		queryKey: KEYS.vendors,
		queryFn: listVendors,
		staleTime: 60_000,
	});
}

export function useVendorAuthCapabilities(vendorKey: string | undefined) {
	return useQuery<VendorAuthCapabilities>({
		queryKey: KEYS.vendor(vendorKey ?? ''),
		queryFn: () => getVendorAuthCapabilities(vendorKey as string),
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

export function useConnectSession(
	sessionId: string | undefined,
	pollToken: string | undefined,
	options?: Partial<UseQueryOptions<ReviewSession>>,
) {
	return useQuery<ReviewSession>({
		queryKey: KEYS.session(sessionId ?? '', pollToken ?? ''),
		queryFn: () => getConnectSession(sessionId as string, pollToken as string),
		enabled: Boolean(sessionId) && Boolean(pollToken),
		staleTime: 5_000,
		// A 403 means the poll_token doesn't match or the session is gone
		// (the backend deliberately conflates the two — no enumeration
		// oracle). Retrying can't fix either, so fail fast; the caller
		// surfaces "the approval link is no longer valid".
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

export function useConfirmConnectSession(sessionId: string, pollToken: string) {
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
export function useBindCredentialToAgents() {
	const client = useQueryClient();
	return useMutation<void, Error, { credentialId: string; agentIds: readonly string[] }>({
		mutationFn: async ({ credentialId, agentIds }) => {
			for (const aid of agentIds) {
				await bindCredentialToAgentBlocked(aid, credentialId);
			}
		},
		onSuccess: (_res, { credentialId, agentIds }) => {
			for (const aid of agentIds) {
				void client.invalidateQueries({
					queryKey: ['agents', aid, 'credential-bindings'],
				});
			}
			void client.invalidateQueries({ queryKey: ['credentials', credentialId, 'bindings'] });
		},
	});
}

/**
 * Combined start+confirm mutation for the user-driven vendor connect flow.
 * The user has already chosen an agent and scopes upfront, so there's no
 * middle "review" step — we open the session and confirm it in one shot,
 * then hand the caller both the session identifiers and the vendor challenge
 * to display. Split lets callers still call the two separately when they
 * need the review page in between (agent-driven flow).
 */
export interface StartAndConfirmVars extends ConnectRequest {
	permission_rules: PermissionRule[];
}

export interface StartAndConfirmResult {
	session_id: string;
	poll_token: string;
	challenge: ConfirmResponse;
}

export function useStartAndConfirmVendorConnect() {
	const client = useQueryClient();
	return useMutation<StartAndConfirmResult, Error, StartAndConfirmVars>({
		mutationFn: async ({ permission_rules, ...connect }) => {
			const started = await startIntegrationConnect(connect);
			const challenge = await confirmConnectSession(started.session_id, started.poll_token, {
				confirmed_scopes: connect.requested_scopes ?? [],
				permission_rules,
			});
			return {
				session_id: started.session_id,
				poll_token: started.poll_token,
				challenge,
			};
		},
		onSuccess: () => {
			// A pending credential row exists on the backend from the moment
			// `:connect` returns — surface it in the credentials list right
			// away so the user can see the pending state.
			void client.invalidateQueries({ queryKey: ['credentials'] });
		},
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
	return useMutation<void, Error, { sessionId: string; pollToken: string }>({
		mutationFn: ({ sessionId, pollToken }) => cancelConnectSession(sessionId, pollToken),
		onSuccess: () => {
			// The credential + session are cascade-deleted on the backend;
			// invalidate so the credentials list drops the stale row.
			void client.invalidateQueries({ queryKey: ['credentials'] });
		},
	});
}

/**
 * Poll the vendor status. Callers set `enabled` to control when polling runs;
 * TanStack Query's `refetchInterval` drives the cadence. On terminal states
 * (connected / failed / expired) the caller should flip `enabled=false`.
 */
export function usePollConnectSessionStatus(
	sessionId: string,
	pollToken: string,
	options?: {
		enabled?: boolean;
		intervalMs?: number;
	},
) {
	return useQuery<StatusResponse>({
		queryKey: KEYS.status(sessionId, pollToken),
		queryFn: () => pollConnectSessionStatus(sessionId, pollToken),
		enabled: options?.enabled ?? true,
		refetchInterval: options?.enabled === false ? false : (options?.intervalMs ?? 3000),
		refetchIntervalInBackground: true,
		staleTime: 0,
		gcTime: 0,
	});
}
