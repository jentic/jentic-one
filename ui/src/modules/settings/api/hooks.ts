/**
 * OAuth clients React Query hooks — backed by the generated OAuthClientsService.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
	OAuthClientsService,
	PermissionsService,
	type OAuthClientCreateRequest,
	type OAuthClientCreateResponse,
	type OAuthClientResponse,
	type OAuthClientRotateSecretResponse,
	type OAuthClientUpdateRequest,
	type PermissionResponse,
} from '@/shared/api';

export type OAuthClient = OAuthClientResponse;

const QUERY_KEY = ['oauth-clients'] as const;
const PERMISSIONS_KEY = ['oauth-clients', 'permissions'] as const;

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
