import { ROUTES } from '@/shared/app/routes';
import {
	consumeCodeVerifier,
	deriveCodeChallenge,
	generateCodeVerifier,
	storeCodeVerifier,
} from '@/shared/auth/pkce';

/**
 * Shared client for the SSO (external-IdP) login flow.
 *
 * The heavy lifting (redirect to the IdP, upstream code exchange, admission
 * policy) lives on the backend. This module only owns the browser half:
 *  1. `beginSsoLogin` mints PKCE + navigates to the platform `/authorize`
 *     endpoint, which redirects on to the configured IdP.
 *  2. After the IdP round-trip the backend redirects the browser back to the
 *     SPA callback (`/app/auth/callback`) with a one-time platform code, which
 *     `SsoCallbackPage` exchanges via `exchangeAuthCode` (see api/idp.ts).
 *
 * The redirect URI is the SPA callback route resolved to an absolute URL — the
 * same value is sent on `/authorize` and presented at `/oauth/token`, so both
 * must agree. `client_id` is the SPA's public client id (no secret; PKCE proves
 * possession).
 */

/** The SPA is a fixed public OAuth client; PKCE (not a secret) authenticates it. */
export const SPA_CLIENT_ID = 'jentic-one-spa';

/**
 * Absolute URL of the SSO callback route, e.g. `https://host/app/auth/callback`.
 * Built from the router basename (Vite `base`, without trailing slash) so it
 * tracks the single source of the `/app` prefix. This is a *SPA route* (under
 * `/app`), where the browser lands after the backend redirect.
 */
export function ssoRedirectUri(): string {
	const base = import.meta.env.BASE_URL.replace(/\/$/, '');
	return `${window.location.origin}${base}${ROUTES.authCallback}`;
}

/**
 * Begin the SSO login: generate PKCE, stash the verifier, then navigate to the
 * platform authorize endpoint (which redirects to the IdP). Returns nothing —
 * on success the browser leaves this document.
 *
 * `/authorize` is a backend API route at the origin root (NOT under the SPA's
 * `/app` base), so it is addressed absolutely from the origin.
 */
export async function beginSsoLogin(): Promise<void> {
	const verifier = generateCodeVerifier();
	const challenge = await deriveCodeChallenge(verifier);
	storeCodeVerifier(verifier);

	const query = new URLSearchParams({
		response_type: 'code',
		client_id: SPA_CLIENT_ID,
		redirect_uri: ssoRedirectUri(),
		code_challenge: challenge,
		code_challenge_method: 'S256',
		scope: 'openid email profile',
	});
	// Full-page navigation (not fetch) so the browser follows the 302 to the IdP.
	window.location.assign(`${window.location.origin}/authorize?${query}`);
}

// Single accessor for the stored verifier, used by the callback page + tests.
export { consumeCodeVerifier };
