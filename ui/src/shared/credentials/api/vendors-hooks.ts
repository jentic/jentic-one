/**
 * Integrations service tier — TanStack Query hooks over the repository.
 *
 * Views (pages/components) call ONLY these hooks; direct fetch calls or
 * `@/shared/api` imports from views are forbidden by ESLint.
 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query';
import {
	cancelConnectSession,
	confirmConnectSession,
	getConnectSession,
	getVendorAuthCapabilities,
	listAgentsForPicker,
	listVendorOperations,
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
	session: (id: string) => ['integrations', 'session', id] as const,
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
	options?: Partial<UseQueryOptions<ReviewSession>>,
) {
	return useQuery<ReviewSession>({
		queryKey: KEYS.session(sessionId ?? ''),
		queryFn: () => getConnectSession(sessionId as string),
		enabled: Boolean(sessionId),
		staleTime: 5_000,
		...options,
	});
}

export function useConfirmConnectSession(sessionId: string) {
	return useMutation<ConfirmResponse, Error, ConfirmRequest>({
		mutationFn: (body) => confirmConnectSession(sessionId, body),
	});
}

/**
 * Fetch operations for a vendor's imported OpenAPI. Polls every 2s while
 * ``data`` is ``null`` (import still queued — the client returns null on
 * 404) so the rules-page preview can render as soon as the import lands.
 * ``enabled`` gates the whole thing to when the caller actually needs it
 * (e.g. only on the rules phase).
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
		] as const,
		queryFn: () =>
			listVendorOperations(api!.vendor, api!.name as string, api!.version as string),
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
			const challenge = await confirmConnectSession(started.session_id, {
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
