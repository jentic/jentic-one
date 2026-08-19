/**
 * OAuth clients React Query hooks.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
	createOAuthClient,
	deactivateOAuthClient,
	listOAuthClients,
	updateOAuthClient,
	type OAuthClient,
	type OAuthClientCreateInput,
	type OAuthClientUpdateInput,
} from './client';

const QUERY_KEY = ['oauth-clients'] as const;

export function useOAuthClients(includeInactive = false) {
	return useQuery({
		queryKey: [...QUERY_KEY, { includeInactive }],
		queryFn: () => listOAuthClients(includeInactive),
	});
}

export function useCreateOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (input: OAuthClientCreateInput) => createOAuthClient(input),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useUpdateOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({ id, input }: { id: string; input: OAuthClientUpdateInput }) =>
			updateOAuthClient(id, input),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useDeactivateOAuthClient() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) => deactivateOAuthClient(id),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export type { OAuthClient, OAuthClientCreateInput, OAuthClientUpdateInput };
