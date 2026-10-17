/**
 * Display helpers for the catalog version the list payload carries
 * (`CatalogEntryResponse.version`).
 */

/** Display form of a version: `v2024-01-01`, `v1.0.0` — never `vv3`. */
export function versionLabel(version: string): string {
	return /^v/i.test(version) ? version : `v${version}`;
}
