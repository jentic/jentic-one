/**
 * OAuth clients React Query hooks — backed by the generated OAuthClientsService.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { OAuthClientCreateRequest } from '@/shared/api/generated/models/OAuthClientCreateRequest';
import type { OAuthClientCreateResponse } from '@/shared/api/generated/models/OAuthClientCreateResponse';
import type { OAuthClientResponse } from '@/shared/api/generated/models/OAuthClientResponse';
import type { OAuthClientRotateSecretResponse } from '@/shared/api/generated/models/OAuthClientRotateSecretResponse';
import type { OAuthClientUpdateRequest } from '@/shared/api/generated/models/OAuthClientUpdateRequest';
import { OAuthClientsService } from '@/shared/api/generated/services/OAuthClientsService';

export type OAuthClient = OAuthClientResponse;

const QUERY_KEY = ['oauth-clients'] as const;

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
	return useMutation({
		mutationFn: (id: string) => OAuthClientsService.rotateOauthClientSecret({ id }),
	});
}

export type {
	OAuthClientCreateRequest,
	OAuthClientCreateResponse,
	OAuthClientRotateSecretResponse,
	OAuthClientUpdateRequest,
};
