/**
 * Service tier — TanStack Query hooks over the OAuth App Registrations
 * repository. Views ONLY talk to the backend through these hooks (module
 * layering rule); a mutation always invalidates the module's cache root so
 * subsequent list/detail reads reflect the write.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { OAuthAppRegistrationFlowKind } from '@/shared/api';
import type {
	OAuthAppRegistrationResponse,
	OAuthAppRegistrationRotateSecretRequest,
	OAuthAppRegistrationUpdateRequest,
} from '@/shared/api';
import {
	createRegistration,
	deleteRegistration,
	fetchRegistrations,
	getRegistration,
	rotateRegistrationSecret,
	updateRegistration,
	type CreateRegistrationInput,
	type ListRegistrationsParams,
	type OAuthAppRegistration,
} from '@/modules/oauth-app-registrations/api/client';

export type { OAuthAppRegistration, CreateRegistrationInput };
export {
	// Re-export as a value so components (which can't reach `@/shared/api`
	// through the module boundary) can still compare `flow_kind` against the
	// enum members.
	OAuthAppRegistrationFlowKind,
};
export type {
	OAuthAppRegistrationResponse,
	OAuthAppRegistrationRotateSecretRequest,
	OAuthAppRegistrationUpdateRequest,
};

/**
 * Module cache root. All list/detail slices share this prefix so any mutation
 * can sweep the whole module with a single `invalidateQueries({ queryKey })`.
 */
const QUERY_KEY = ['oauth-app-registrations'] as const;

export function useOAuthAppRegistrations(params: ListRegistrationsParams = {}) {
	return useQuery<OAuthAppRegistrationResponse[]>({
		queryKey: [...QUERY_KEY, 'list', params],
		queryFn: () => fetchRegistrations(params),
	});
}

export function useOAuthAppRegistration(id: string | null) {
	return useQuery<OAuthAppRegistrationResponse>({
		queryKey: [...QUERY_KEY, 'detail', id],
		queryFn: () => getRegistration(id as string),
		enabled: id != null,
	});
}

export function useCreateOAuthAppRegistration() {
	const qc = useQueryClient();
	return useMutation<OAuthAppRegistrationResponse, Error, CreateRegistrationInput>({
		mutationFn: (input) => createRegistration(input),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useUpdateOAuthAppRegistration() {
	const qc = useQueryClient();
	return useMutation<
		OAuthAppRegistrationResponse,
		Error,
		{ id: string; input: OAuthAppRegistrationUpdateRequest }
	>({
		mutationFn: ({ id, input }) => updateRegistration(id, input),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useRotateOAuthAppRegistrationSecret() {
	const qc = useQueryClient();
	return useMutation<OAuthAppRegistrationResponse, Error, { id: string; clientSecret: string }>({
		mutationFn: ({ id, clientSecret }) =>
			rotateRegistrationSecret(id, { client_secret: clientSecret }),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

export function useDeleteOAuthAppRegistration() {
	const qc = useQueryClient();
	return useMutation<void, Error, string>({
		mutationFn: (id) => deleteRegistration(id),
		onSuccess: () => {
			void qc.invalidateQueries({ queryKey: QUERY_KEY });
		},
	});
}

/**
 * The platform's OAuth callback redirect URI — what an admin must paste into
 * the vendor's OAuth-app console for the authorization-code flow. Sourced
 * from the running config's `credentials.providers.direct_oauth2.redirect_uri`.
 *
 * TODO(feat/admin-oauth-app-registrations): swap this stub for a real read
 * once the backend exposes an endpoint that returns the configured redirect
 * URI (see the direct_oauth2 provider config module). Until then we derive a
 * best-guess from `window.location.origin` so the field is populated.
 */
export function usePlatformRedirectUri(): { redirectUri: string; isStub: true } {
	const origin =
		typeof window !== 'undefined' && window.location?.origin
			? window.location.origin
			: 'https://example.invalid';
	return { redirectUri: `${origin}/auth/callback`, isStub: true };
}
