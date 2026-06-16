/**
 * Helpers for surfacing fetch errors to the UI without leaking raw HTML
 * bodies from misbehaving proxies or framework error pages.
 *
 * The convention across the app used to be `throw new Error(await r.text())`,
 * which routes whatever the server returned — JSON, plain text, a 502 HTML
 * page from a reverse proxy — straight into ErrorAlert. That's a bad UX:
 * users see ``<!doctype html><html...>`` rendered as a banner, and the real
 * problem (HTTP status + a short reason) is buried.
 *
 * Instead, prefer:
 *
 * ```ts
 * if (!r.ok) throw await parseApiError(r);
 * ```
 *
 * which yields:
 *
 *   - JSON `{ detail: "..." }` → "..."
 *   - JSON `{ detail: { error_description: "..." } }` (OAuth shape) → "..."
 *   - Anything else → "Request failed (HTTP {status})"
 */

interface ApiError extends Error {
	status: number;
}

export async function parseApiError(response: Response, fallback?: string): Promise<ApiError> {
	let message: string | null = null;
	try {
		const ct = response.headers.get('content-type') ?? '';
		if (ct.includes('application/json')) {
			const body: unknown = await response.json();
			message = extractMessage(body);
		}
	} catch {
		// JSON parse / read failed — fall through to the status-only message.
	}

	const finalMessage = message ?? fallback ?? `Request failed (HTTP ${response.status})`;
	const err = new Error(finalMessage) as ApiError;
	err.status = response.status;
	return err;
}

function extractMessage(body: unknown): string | null {
	if (!body || typeof body !== 'object') return null;
	const obj = body as Record<string, unknown>;

	const detail = obj.detail;
	if (typeof detail === 'string') return detail;
	if (detail && typeof detail === 'object') {
		const inner = detail as Record<string, unknown>;
		if (typeof inner.error_description === 'string') return inner.error_description;
		if (typeof inner.error === 'string') return inner.error;
	}

	if (typeof obj.error_description === 'string') return obj.error_description;
	if (typeof obj.error === 'string') return obj.error;
	if (typeof obj.message === 'string') return obj.message;

	return null;
}

/**
 * Turn a *thrown* error from the generated OpenAPI client (`ApiError`, which
 * already carries `{ status, statusText, body }`) — or any unknown error — into
 * a short, human-readable message suitable for `ErrorAlert`/`toast`.
 *
 * The generated client's own `.message` is the unhelpful
 * `"Generic Error: status: 409; status text: Conflict; body: {...}"`, so we
 * prefer the parsed `body` (FastAPI's `{ detail }`) and fall back to a
 * status-specific phrase before ever exposing that raw string.
 */
export function messageFromApiError(err: unknown, fallback?: string): string {
	if (!err) return fallback ?? 'An unknown error occurred.';
	const e = err as { status?: number; statusText?: string; body?: unknown; message?: unknown };

	const fromBody = extractMessage(e.body);
	if (fromBody) return fromBody;

	switch (e.status) {
		case 401:
			return 'Not authenticated — please log in first.';
		case 403:
			return 'You do not have permission to perform this action.';
		case 404:
			return 'Not found — it may have already been removed.';
		case 409:
			return 'Conflict — this may have already been done.';
	}

	if (typeof e.message === 'string' && e.message && !e.message.startsWith('Generic Error:')) {
		return e.message;
	}
	if (e.statusText && e.status) return `${e.status}: ${e.statusText}`;
	return fallback ?? `Unexpected error (HTTP ${e.status ?? '?'})`;
}
