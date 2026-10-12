// Pure API-identity keys and coverage. Framework-free, so the picker's selection
// set, the add-APIs preflight and the tile grid key the same API the same way.
import { slugifyApiField } from '@/shared/lib/apiSlug';

/**
 * The `name` seed a catalog import registers for an entry, before slugifying:
 * a `domain/sub` id seeds its **sub segment** (`github.com/api.github.com` →
 * `api.github.com`), a bare-domain id seeds itself (`coincap.io`). The vendor
 * half travels separately, so the whole id would fuse it into the name. Mirrors
 * the importer's `catalog_api_name`, except that the importer falls back to the
 * whole id when two entries' sub segments collide under one vendor — the
 * catalog list here can't see that, so a credential created for such an entry
 * before it is imported may need re-scoping. Once it is imported, the server
 * gives a credential carrying its `catalog_api_id` the registered name.
 */
export function catalogApiNameSeed(apiId: string): string {
	const slash = apiId.indexOf('/');
	const sub = slash === -1 ? '' : apiId.slice(slash + 1);
	return sub ? sub : apiId;
}

/**
 * The canonical `vendor`/`name` a catalog import registers for an entry: the
 * entry's `vendor` (its registrable domain, `stripe.com`) and its name seed
 * ({@link catalogApiNameSeed}), both slugified (`github-com`, `api-github-com`)
 * — the identity `ApiPicker` also gives a credential saved from a catalog pick.
 * The version comes from the spec at import time, so it isn't known here.
 *
 * Null without a catalog `vendor`: the importer then falls back to the spec's
 * own `info` block, which the catalog list doesn't carry.
 */
export function catalogImportRef(entry: {
	apiId: string;
	vendor?: string | null;
}): { vendor: string; name: string } | null {
	const vendor = entry.vendor?.trim();
	if (!vendor || !entry.apiId.trim()) return null;
	return {
		vendor: slugifyApiField(vendor),
		name: slugifyApiField(catalogApiNameSeed(entry.apiId.trim())),
	};
}

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

/**
 * How far a credential's stored scope reaches, by the same wildcard rule as
 * `apiScopeCovers`: `vendor-wide` (no API name — every API of the vendor, every
 * version), `any-version` (an API, every version — including ones added
 * later), or `pinned` (one API, one version).
 */
export function apiScopeReach(scope: ApiScope): 'vendor-wide' | 'any-version' | 'pinned' {
	if (!scope.name?.trim()) return 'vendor-wide';
	if (!scope.version?.trim()) return 'any-version';
	return 'pinned';
}

/**
 * The workspace API a credential is for, matched version-aside: by catalog id
 * when both carry one, else by vendor + API name in slug form. Null for a
 * vendor-wide credential (no API name) or one whose API isn't imported.
 */
export function workspaceApiForCredential<
	T extends { catalog_api_id?: string | null; api: { vendor: string; name: string } },
>(
	cred: { catalog_api_id?: string | null; api: { vendor: string; name?: string | null } },
	apis: readonly T[],
): T | null {
	const catalogId = cred.catalog_api_id?.trim().toLowerCase();
	if (catalogId) {
		const hit = apis.find((a) => a.catalog_api_id?.trim().toLowerCase() === catalogId);
		if (hit) return hit;
	}
	const name = cred.api.name?.trim();
	if (!name) return null;
	const vendor = slugifyApiField(cred.api.vendor);
	const nameSlug = slugifyApiField(name);
	return (
		apis.find(
			(a) =>
				slugifyApiField(a.api.vendor) === vendor &&
				slugifyApiField(a.api.name) === nameSlug,
		) ?? null
	);
}
