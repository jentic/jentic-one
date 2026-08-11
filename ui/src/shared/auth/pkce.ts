/**
 * PKCE (RFC 7636) helpers for the browser SSO login flow.
 *
 * The SPA is a public OAuth client, so it uses Authorization-Code + PKCE: it
 * generates a random `code_verifier`, derives the S256 `code_challenge`, sends
 * the challenge on `GET /authorize`, and later proves possession by presenting
 * the verifier at `POST /oauth/token`. The verifier is held in sessionStorage
 * across the IdP redirect round-trip (it never leaves this origin).
 *
 * Uses Web Crypto (`crypto.subtle`) — available in all supported browsers and
 * in the jsdom test env via the polyfilled global.
 */

const VERIFIER_KEY = 'jentic-one.pkce_verifier';

/** Base64url-encode bytes without padding (RFC 7636 §A). */
function base64UrlEncode(bytes: Uint8Array): string {
	let binary = '';
	for (const b of bytes) binary += String.fromCharCode(b);
	return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Generate a high-entropy `code_verifier` (43–128 chars, RFC 7636 §4.1). */
export function generateCodeVerifier(): string {
	const bytes = new Uint8Array(32);
	crypto.getRandomValues(bytes);
	return base64UrlEncode(bytes);
}

/** Derive the S256 `code_challenge` for a verifier. */
export async function deriveCodeChallenge(verifier: string): Promise<string> {
	const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
	return base64UrlEncode(new Uint8Array(digest));
}

/**
 * Persist the verifier for the redirect round-trip. sessionStorage (not local)
 * so it dies with the tab and never outlives the single login attempt.
 */
export function storeCodeVerifier(verifier: string): void {
	try {
		window.sessionStorage.setItem(VERIFIER_KEY, verifier);
	} catch {
		// Sandboxed/private contexts: the callback will find no verifier and the
		// exchange fails closed with a normal "sign-in failed" message.
	}
}

/** Read-and-clear the stored verifier (single-use). */
export function consumeCodeVerifier(): string | null {
	try {
		const v = window.sessionStorage.getItem(VERIFIER_KEY);
		if (v !== null) window.sessionStorage.removeItem(VERIFIER_KEY);
		return v;
	} catch {
		return null;
	}
}
