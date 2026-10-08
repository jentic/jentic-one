/**
 * The human-readable `detail` of an RFC 9457 problem body, or `null`.
 *
 * Only a non-blank string counts: FastAPI's 422 `detail` is a list of field
 * errors, and a problem without a `detail` has nothing more specific to say
 * than the caller's own fallback.
 */
export function problemDetailText(body: unknown): string | null {
	if (typeof body !== 'object' || body === null) return null;
	const detail = (body as { detail?: unknown }).detail;
	return typeof detail === 'string' && detail.trim() ? detail : null;
}
