/**
 * Normalize an API vendor/name field to its canonical slug form — the mirror of
 * the backend's `slugify_api_field`: lowercase, trim, collapse runs of
 * non-`[a-z0-9-]` to a single hyphen, trim hyphens, truncate at 100.
 *
 * Vendors and names are slugified on write, so an exact-match server-side filter
 * needs the slug rather than the raw value a filer sent (`httpbin.org`), and two
 * spellings of one API only compare equal in this form. Idempotent, so an
 * already-canonical value passes through unchanged.
 */
export function slugifyApiField(value: string): string {
	return value
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9-]+/g, '-')
		.replace(/^-+|-+$/g, '')
		.slice(0, 100);
}
