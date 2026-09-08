/**
 * Bearer-JWT token store.
 *
 * jentic-one auth is stateless Bearer-JWT (HS256): `POST /auth/login` returns
 * an `access_token` that must be sent as `Authorization: Bearer <token>` on
 * every protected request. There is no logout endpoint — sign-out is purely
 * client-side disposal of the token (see the auth recon contract).
 *
 * The token is held in memory (the source of truth for the request layer) and
 * mirrored to localStorage so a page refresh keeps the session. Subscribers are
 * notified on every change so the auth context can re-render and the generated
 * client can pick up the new value via its TOKEN resolver.
 *
 * Alongside the token we track when it expires (`expires_in` from the login /
 * refresh response) so the auth context can renew the session before it dies,
 * and a one-shot "session expired" notice (sessionStorage) so the login page
 * can tell the user *why* they were signed out instead of showing a bare form.
 */
const STORAGE_KEY = 'jentic-one.access_token';
const EXPIRES_AT_KEY = 'jentic-one.access_token_expires_at';
const SESSION_EXPIRED_KEY = 'jentic-one.session_expired';

/** Why a token was cleared. Only rejection/expiry-driven clears carry a reason. */
export type ClearTokenReason = 'session-expired';

type Listener = (token: string | null) => void;

let current: string | null = readPersisted(STORAGE_KEY);
let expiresAt: number | null = readPersistedExpiry();
const listeners = new Set<Listener>();

function readPersisted(key: string): string | null {
	try {
		return window.localStorage.getItem(key);
	} catch {
		// localStorage can throw in private-mode / sandboxed contexts — fall back
		// to in-memory only rather than crashing the app.
		return null;
	}
}

function readPersistedExpiry(): number | null {
	const raw = readPersisted(EXPIRES_AT_KEY);
	if (raw === null) return null;
	const parsed = Number(raw);
	return Number.isFinite(parsed) ? parsed : null;
}

function persist(key: string, value: string | null): void {
	try {
		if (value === null) {
			window.localStorage.removeItem(key);
		} else {
			window.localStorage.setItem(key, value);
		}
	} catch {
		// Ignore persistence failures; the in-memory token still works for the
		// current page session.
	}
}

export function getToken(): string | null {
	return current;
}

/**
 * Epoch-ms timestamp when the current token expires, or null when unknown
 * (no token, or a token adopted through the legacy `setToken` path).
 */
export function getSessionExpiresAt(): number | null {
	return current === null ? null : expiresAt;
}

export function setToken(token: string | null): void {
	if (current === token) return;
	current = token;
	persist(STORAGE_KEY, token);
	// The legacy path carries no expiry metadata — drop any previous session's
	// so getSessionExpiresAt() never reports a stale timestamp for a new token.
	expiresAt = null;
	persist(EXPIRES_AT_KEY, null);
	for (const listener of listeners) listener(token);
}

/**
 * Adopt a token bundle from login/refresh, recording when it expires so the
 * auth context can schedule a proactive renewal. Also clears any pending
 * session-expired notice — a fresh session supersedes it.
 */
export function setSession(token: string, expiresInSeconds: number): void {
	expiresAt = Date.now() + expiresInSeconds * 1000;
	persist(EXPIRES_AT_KEY, String(expiresAt));
	clearSessionExpiredNotice();
	if (current === token) return;
	current = token;
	persist(STORAGE_KEY, token);
	for (const listener of listeners) listener(token);
}

export function clearToken(reason?: ClearTokenReason): void {
	if (reason === 'session-expired') {
		try {
			window.sessionStorage.setItem(SESSION_EXPIRED_KEY, '1');
		} catch {
			// Best-effort: without sessionStorage the login page simply shows no
			// expiry notice.
		}
	}
	setToken(null);
}

/**
 * One-shot read of the session-expired notice (set by rejection/expiry-driven
 * `clearToken('session-expired')` calls). Consuming clears it so a later,
 * unrelated visit to the login page doesn't repeat the stale message.
 */
export function consumeSessionExpiredNotice(): boolean {
	try {
		const flagged = window.sessionStorage.getItem(SESSION_EXPIRED_KEY) !== null;
		if (flagged) window.sessionStorage.removeItem(SESSION_EXPIRED_KEY);
		return flagged;
	} catch {
		return false;
	}
}

function clearSessionExpiredNotice(): void {
	try {
		window.sessionStorage.removeItem(SESSION_EXPIRED_KEY);
	} catch {
		// Ignore — see consumeSessionExpiredNotice.
	}
}

/** Subscribe to token changes. Returns an unsubscribe function. */
export function subscribeToken(listener: Listener): () => void {
	listeners.add(listener);
	return () => {
		listeners.delete(listener);
	};
}
