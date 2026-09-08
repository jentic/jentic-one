import { OpenAPI } from '@/shared/api/generated/core/OpenAPI';
import { request as __request } from '@/shared/api/generated/core/request';
import type { TokenResponse } from '@/shared/api/generated/models/TokenResponse';
import '@/shared/api/client';

/**
 * External-IdP login capability descriptor (public, secret-free). The SPA reads
 * this before login to decide whether to render a "Continue with <provider>"
 * button. Mirrors the backend `GET /auth/idp` response.
 */
export interface IdpDescriptor {
	enabled: boolean;
	provider: string | null;
}

/** Fetch the public IdP-login descriptor. Unauthenticated. */
export function getIdpDescriptor(): Promise<IdpDescriptor> {
	return __request<IdpDescriptor>(OpenAPI, {
		method: 'GET',
		url: '/auth/idp',
	});
}

/**
 * Exchange an authorization code + PKCE verifier for a session bundle at the
 * platform token endpoint (public client — no client_secret). Returns the same
 * `{ access_token, expires_in }` shape the password login yields, so the auth
 * context adopts it through the identical `setSession` path.
 */
export function exchangeAuthCode(params: {
	code: string;
	codeVerifier: string;
	redirectUri: string;
	clientId: string;
}): Promise<TokenResponse> {
	return __request<TokenResponse>(OpenAPI, {
		method: 'POST',
		url: '/oauth/token',
		body: {
			grant_type: 'authorization_code',
			code: params.code,
			code_verifier: params.codeVerifier,
			redirect_uri: params.redirectUri,
			client_id: params.clientId,
		},
		mediaType: 'application/json',
	});
}
