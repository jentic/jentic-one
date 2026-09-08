/**
 * OAuth clients React Query hooks — backed by the generated OAuthClientsService.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
	OAuthClientsService,
	PermissionsService,
	sharedQueryKeys,
	type OAuthClientCreateRequest,
	type OAuthClientCreateResponse,
	type OAuthClientResponse,
	type OAuthClientRotateSecretResponse,
	type OAuthClientUpdateRequest,
	type PermissionResponse,
} from '@/shared/api';
import { useAgentStreamOptional } from '@/shared/lib';

export type OAuthClient = OAuthClientResponse;

// Derived from the shared cross-module root: the agent-stream provider
// invalidates `sharedQueryKeys.oauthClientsRoot` when a live `oauth_client.*` /
// `oauth_grant.*` event lands, which must hit these slices.
const QUERY_KEY = sharedQueryKeys.oauthClientsRoot;
const QUEUE_KEY = [...QUERY_KEY, 'queue'] as const;
// The permission catalogue is NOT client data — it gets its own root (like the
// agents module's `permissionsKey`) so the oauth-clients invalidation fan-out
// (every client mutation + every live `oauth_client.*`/`oauth_grant.*` event
// sweeps `oauthClientsRoot`) doesn't pointlessly refetch `GET /permissions`.
const PERMISSIONS_KEY = ['settings-oauth-permissions'] as const;

export function usePermissionCatalogue() {
	return useQuery<PermissionResponse[]>({
		queryKey: PERMISSIONS_KEY,
		queryFn: () => PermissionsService.listPermissions().then((r) => r.data),
		staleTime: 5 * 60 * 1000,
	});
}

export function useOAuthClients(includeInactive = false) {
	return useQuery({
		queryKey: [...QUERY_KEY, { includeInactive }],
		queryFn: () =>
			OAuthClientsService.listOauthClients({ includeInactive }).then((r) => r.data),
	});
}

/**
 * The D7 approval queue: DCR registrations awaiting a
 * decision, or previously denied rows (deny is reversible — a later approve
 * un-bricks the client). `approval_status=pending|denied` implies
 * `include_inactive` server-side, so no flag is needed here.
 */
export function useOAuthClientQueue(approvalStatus: 'pending' | 'denied' = 'pending') {
	return useQuery({
		queryKey: [...QUEUE_KEY, approvalStatus],
		queryFn: () => OAuthClientsService.listOauthClients({ approvalStatus }).then((r) => r.data),
	});
}

/** Approve a client (D7: pending→approved, or the denied→approved recovery). */
export function useApproveOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => OAuthClientsService.approveOauthClient({ id }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

/** Deny a client (D7: the row is kept, so approve can reverse the decision). */
export function useDenyOAuthClient() {
	const qc = useQueryClient();
	// A deny emits no SSE event (unlike approve, whose `oauth_client.approved`
	// event settles the rail's actionable row via the stream mirror), so this
	// mutation settles the `oauth_client.registered` row itself — it knows the
	// client id. Provider-optional: tests and embedded surfaces without the
	// rail's stream still work.
	const stream = useAgentStreamOptional();
	return useMutation({
		mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
			OAuthClientsService.denyOauthClient({
				id,
				requestBody: reason ? { reason } : undefined,
			}),
		onSuccess: (_data, { id }) => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
			stream?.settleOAuthClientRegistration(id);
		},
	});
}

export function useCreateOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (input: OAuthClientCreateRequest) =>
			OAuthClientsService.createOauthClient({ requestBody: input }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useUpdateOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, input }: { id: string; input: OAuthClientUpdateRequest }) =>
			OAuthClientsService.updateOauthClient({ id, requestBody: input }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useDeactivateOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => OAuthClientsService.deactivateOauthClient({ id }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useReactivateOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) =>
			OAuthClientsService.updateOauthClient({ id, requestBody: { active: true } }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useRotateOAuthClientSecret() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => OAuthClientsService.rotateOauthClientSecret({ id }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export type {
	OAuthClientCreateRequest,
	OAuthClientCreateResponse,
	OAuthClientRotateSecretResponse,
	OAuthClientUpdateRequest,
};
