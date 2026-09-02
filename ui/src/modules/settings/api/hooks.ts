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

export type OAuthClient = OAuthClientResponse;

// Derived from the shared cross-module root: the agent-stream provider
// invalidates `sharedQueryKeys.oauthClientsRoot` when a live `oauth_client.*` /
// `oauth_grant.*` event lands (phase-3a §4.8), which must hit these slices.
const QUERY_KEY = sharedQueryKeys.oauthClientsRoot;
const QUEUE_KEY = [...QUERY_KEY, 'queue'] as const;
const PERMISSIONS_KEY = [...QUERY_KEY, 'permissions'] as const;

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
 * The D7 approval queue (phase-3a §4.8): DCR registrations awaiting a
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
	return useMutation({
		mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
			OAuthClientsService.denyOauthClient({
				id,
				requestBody: reason ? { reason } : undefined,
			}),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
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
