import { ROUTES } from '@/shared/app/routes';

/** Location fields the AuthGuard remembers when it bounces a visit to sign-in. */
export interface ReturnLocation {
	pathname?: unknown;
	search?: unknown;
	hash?: unknown;
}

// Throwaway origin for parsing: a candidate is same-origin exactly when it
// resolves against this base without changing the origin.
const PARSE_BASE = 'https://app.invalid';

/**
 * Resolve where the login page sends the user after signing in: the full
 * remembered location (path, query and hash), so a deep link such as
 * `/agents?approve=<id>` keeps its state.
 *
 * Only a basename-relative path inside the app is accepted. Anything that could
 * leave the origin — an absolute URL, a protocol-relative `//host` path, a
 * backslash variant browsers normalise to `//`, or a non-`/` start — falls back
 * to the app home, as does a return to the login page itself.
 */
export function resolveReturnTo(from: ReturnLocation | null | undefined): string {
	const pathname = from?.pathname;
	if (typeof pathname !== 'string' || !isAppRelativePath(pathname)) return ROUTES.app;
	if (pathname === ROUTES.login) return ROUTES.app;

	const search =
		typeof from?.search === 'string' && from.search.startsWith('?') ? from.search : '';
	const hash = typeof from?.hash === 'string' && from.hash.startsWith('#') ? from.hash : '';
	const target = `${pathname}${search}${hash}`;

	let parsed: URL;
	try {
		parsed = new URL(target, PARSE_BASE);
	} catch {
		return ROUTES.app;
	}
	if (parsed.origin !== PARSE_BASE) return ROUTES.app;
	return target;
}

function isAppRelativePath(pathname: string): boolean {
	if (!pathname.startsWith('/')) return false;
	// `//host` and `/\host` are protocol-relative to a browser.
	const second = pathname.charAt(1);
	if (second === '/' || second === '\\') return false;
	// Control characters (tab, newline) are stripped by URL parsing and can
	// smuggle a `//` past the check above.
	for (let i = 0; i < pathname.length; i++) {
		const code = pathname.charCodeAt(i);
		if (code < 0x20 || code === 0x7f) return false;
	}
	return true;
}
