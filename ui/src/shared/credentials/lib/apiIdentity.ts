// Pure API-identity keys and coverage. Framework-free, so the picker's selection
// set, the add-APIs preflight and the tile grid key the same API the same way.
import { slugifyApiField } from '@/shared/lib/apiSlug';

/** Canonical `vendor/name` identity key — the picker's selection key. */
export function apiRefKey(ref: { vendor: string; name: string }): string {
	return `${slugifyApiField(ref.vendor)}/${slugifyApiField(ref.name)}`;
}

/**
 * An API identity whose `name`/`version` axes may be wildcards — both a
 * credential's stored scope and a binding's served reference. An axis wildcards
 * when `null` or empty: the backend stores NULL and serialises it as `""`.
 */
export interface ApiScope {
	vendor: string;
	name?: string | null;
	version?: string | null;
}

/** A concrete API identity — a workspace row or a picked API, never wildcarded. */
export interface ConcreteApiRef {
	vendor: string;
	name: string;
	version?: string | null;
}

/**
 * Does `scope` cover the concrete API `ref`? Mirrors the backend's
 * `credential_covers`, the authority the broker resolves a binding against — a UI
 * that answers differently offers a credential the broker then refuses.
 * `vendor`/`name` compare in canonical slug form, the way the backend does, so
 * the raw and stored spellings of one API (`httpbin.org`, `httpbin-org`) cover
 * each other; `version` is only trimmed, since a pinned version does not cover
 * another revision and slugifying would corrupt it (`1.1.4` → `1-1-4`).
 */
export function apiScopeCovers(scope: ApiScope, ref: ConcreteApiRef): boolean {
	if (slugifyApiField(scope.vendor) !== slugifyApiField(ref.vendor)) return false;
	const name = scope.name?.trim();
	if (name && slugifyApiField(name) !== slugifyApiField(ref.name)) return false;
	const version = scope.version?.trim();
	if (version && version !== (ref.version?.trim() ?? '')) return false;
	return true;
}
