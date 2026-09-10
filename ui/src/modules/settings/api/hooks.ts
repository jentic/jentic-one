/**
 * OAuth clients React Query hooks — backed by the generated OAuthClientsService.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
	ApiError,
	AuditService,
	AuditTargetType,
	OAuthClientCreateRequest,
	OAuthClientsService,
	OAuthService,
	PermissionsService,
	sharedQueryKeys,
	SystemService,
	type AuditResponse,
	type InstanceIdentityResponse,
	type OAuthClientCreateResponse,
	type OAuthClientResponse,
	type OAuthClientRotateSecretResponse,
	type OAuthClientUpdateRequest,
	type OAuthGrantAdminListResponse,
	type OAuthGrantAdminResponse,
	type PermissionResponse,
} from '@/shared/api';
import { useAgentStreamOptional } from '@/shared/lib';

export type OAuthClient = OAuthClientResponse;
export type OAuthClientGrant = OAuthGrantAdminResponse;
export type OAuthClientAuditEntry = AuditResponse;
// Value re-export (enum namespaces for `consent_model` /
// `token_endpoint_auth_method`) so views build create payloads without
// touching the @/shared/api facade directly (module-boundary lint rule).
export { OAuthClientCreateRequest };

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

// Instance identity is deployment metadata, not client data — own root for
// the same fan-out reason as the permission catalogue above.
const INSTANCE_KEY = ['settings-instance-identity'] as const;

/**
 * The instance's self-described identity (`GET /instance`, unauthenticated):
 * the deployment's canonical base URL and whether the daemon-native HTTP MCP
 * endpoint is enabled — everything the "Connect an MCP client" card (#1249)
 * can honestly derive. A thin sibling of the agents module's hook over the
 * same generated service (modules never import each other — boundary rule).
 */
export function useInstanceIdentity() {
	return useQuery<InstanceIdentityResponse>({
		queryKey: INSTANCE_KEY,
		queryFn: () => SystemService.getInstance(),
		staleTime: 5 * 60 * 1000,
	});
}

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

/**
 * One client's fresh row (`GET /admin/oauth-clients/{id}`) — the detail
 * sheet's live source. The sheet is seeded from the roster row the user
 * clicked, but mutations (approve, rotate, deactivate) land between open and
 * close, so it re-reads under the shared root (every mutation above sweeps
 * `QUERY_KEY`, which covers this slice too).
 */
export function useOAuthClient(id: string | null) {
	return useQuery<OAuthClientResponse>({
		queryKey: [...QUERY_KEY, 'detail', id],
		queryFn: () => OAuthClientsService.getOauthClient({ id: id as string }),
		enabled: id != null,
	});
}

/**
 * The consent→agent grants held against ONE client — the admin cross-view
 * (`GET /admin/oauth-grants?client_id=…`) filtered to the detail sheet's
 * client. Mirrors the agents module's `useAgentOauthGrants` grammar:
 * cursor-paginated behind "Load more", status-sliced (`null` = all).
 * Keyed under the shared oauth-clients root so client mutations and live
 * `oauth_grant.*` events sweep it along with the roster.
 */
export function useOAuthClientGrants(
	clientId: string | null,
	status: 'active' | 'revoked' | null = 'active',
) {
	return useInfiniteQuery<OAuthGrantAdminListResponse>({
		queryKey: [...QUERY_KEY, 'grants', clientId ?? '', status ?? 'all'],
		queryFn: ({ pageParam }) =>
			OAuthService.listOauthGrants({
				clientId,
				status,
				cursor: (pageParam as string | null) ?? null,
			}),
		initialPageParam: null,
		getNextPageParam: (last) => (last.has_more ? (last.next_cursor ?? null) : null),
		enabled: clientId != null,
	});
}

/**
 * Revoke a consent→agent grant (§4.6 kill switch) from the client detail
 * sheet. Sweeps the oauth-clients root (the grants slices above + the
 * roster's per-client `active_grant_count`) and the shared oauth-grants root
 * so the agents module's per-agent "Connected clients" panel doesn't go
 * stale when an admin revokes from here.
 */
export function useRevokeOAuthClientGrant() {
	const qc = useQueryClient();
	return useMutation<void, Error, string>({
		mutationFn: (grantId: string) => OAuthService.revokeOauthGrant({ grantId }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
			void qc.invalidateQueries({ queryKey: sharedQueryKeys.oauthGrantsRoot });
		},
	});
}

/**
 * Client-scoped audit trail for the detail sheet's "Recent changes" panel —
 * approve/deny (with the operator's reason), deactivate, rotate, and DCR
 * re-queue all write rows with `target_type=oauth_client`. Mirrors the agents
 * module's `useActorAudit`: 401/403 resolve to an empty list so the panel
 * degrades to its quiet empty state for non-admins instead of erroring.
 * Keyed under the shared root so decision mutations refresh the trail.
 */
export function useOAuthClientAudit(clientId: string | null) {
	return useQuery<AuditResponse[]>({
		queryKey: [...QUERY_KEY, 'audit', clientId],
		queryFn: async () => {
			try {
				const res = await AuditService.listAuditEntries({
					targetType: AuditTargetType.OAUTH_CLIENT,
					targetId: clientId as string,
					limit: 25,
				});
				return res.data;
			} catch (error) {
				if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
					return [];
				}
				throw error;
			}
		},
		enabled: clientId != null,
		staleTime: 30 * 1000,
	});
}

export type {
	OAuthClientCreateResponse,
	OAuthClientRotateSecretResponse,
	OAuthClientUpdateRequest,
};
