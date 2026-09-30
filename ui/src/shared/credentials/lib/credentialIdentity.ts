// What tells one credential apart from another. An API can hold several, and
// their names often repeat the API's own ("airlabs.co" three times), so every
// surface that lists siblings leans on the same facts: auth type, when it was
// added, and a short id tail.
import { slugifyApiField } from '@/shared/lib/apiSlug';
import { CREDENTIAL_TYPE_LABELS, CredentialType, type Credential } from '@/shared/credentials/api';

/** Characters of the credential id shown as its tail — enough to tell siblings apart. */
const ID_TAIL_LENGTH = 6;

/** `added 23 Sept 2026`-style date; `recently` when the backend sent none. */
export function formatCredentialDate(value: string | null | undefined): string {
	if (!value) return 'recently';
	const d = new Date(value);
	if (Number.isNaN(d.getTime())) return 'recently';
	return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** The last few characters of the id, the one fact two same-named siblings never share. */
export function credentialIdTail(cred: Pick<Credential, 'credential_id'>): string {
	return cred.credential_id.slice(-ID_TAIL_LENGTH);
}

/** `API key · added 23 Sept 2026 · …a1b2c3` — one line that tells siblings apart.
 * `type: false` drops the type, for a host that already shows it as a badge. */
export function credentialDistinguisher(
	cred: Pick<Credential, 'credential_id' | 'type' | 'created_at'>,
	{ type = true }: { type?: boolean } = {},
): string {
	return [
		type ? (CREDENTIAL_TYPE_LABELS[cred.type] ?? cred.type) : null,
		`added ${formatCredentialDate(cred.created_at)}`,
		`…${credentialIdTail(cred)}`,
	]
		.filter(Boolean)
		.join(' · ');
}

/**
 * Group key for "the same API": the vendor slug, the exact stored API name and —
 * when the credential carries one — its catalog API id. Two APIs that happen to
 * slug alike stay apart (the name is compared as stored, and a catalog id tells
 * catalog entries apart). The version is left out so a credential re-added for a
 * new revision lands beside its predecessor; the group shows each row's version.
 */
export function credentialApiGroupKey(cred: Pick<Credential, 'api' | 'catalog_api_id'>): string {
	const vendor = slugifyApiField(cred.api.vendor);
	const name = (cred.api.name ?? '').trim().toLowerCase();
	const catalogId = cred.catalog_api_id?.trim().toLowerCase() ?? '';
	return `${vendor}/${name}|${catalogId}`;
}

/** Which credential an API should use: an existing one, or a new one even though
 * existing ones cover it. An API can hold any number of credentials. */
export type CredentialChoice = { kind: 'existing'; credentialId: string } | { kind: 'new' };

/**
 * True when an OAuth 2.0 authorization-code credential's sign-in has not
 * completed — the only not-usable case a redacted credential can prove.
 */
export function credentialAwaitsConsent(credential: Credential | undefined): boolean {
	if (!credential || credential.type !== CredentialType.OAUTH2) return false;
	const details = credential.details;
	if (!details || typeof details !== 'object') return false;
	return details.grant_type === 'authorization_code' && details.connected === false;
}

/** The API an access-request item names; `name`/`version` left open mean "any". */
export interface ApiReference {
	vendor: string;
	name?: string | null;
	version?: string | null;
}

/**
 * The active credentials that serve `ref`, the way the platform resolves a bind
 * that names no credential: an open axis on the reference matches any value, a
 * credential with no API name serves its whole vendor, and credentials pinned to
 * the exact name win over vendor-wide ones.
 */
export function credentialsServingReference(
	credentials: readonly Credential[],
	ref: ApiReference,
): Credential[] {
	const vendor = slugifyApiField(ref.vendor);
	const name = ref.name?.trim() ? slugifyApiField(ref.name) : null;
	const version = ref.version?.trim() || null;
	const serving = credentials.filter((cred) => {
		if (!cred.active || slugifyApiField(cred.api.vendor) !== vendor) return false;
		const credName = cred.api.name?.trim() ? slugifyApiField(cred.api.name) : null;
		if (name && credName && credName !== name) return false;
		const credVersion = cred.api.version?.trim() || null;
		return !version || !credVersion || credVersion === version;
	});
	if (!name) return serving;
	const exact = serving.filter((cred) => cred.api.name?.trim());
	return exact.length > 0 ? exact : serving;
}

/** How two credential names compare: case and surrounding space never tell them apart. */
function nameKey(name: string): string {
	return name.trim().toLowerCase();
}

/**
 * The credentials a new one for `vendor`/`name` would sit beside — the same
 * vendor, and the same API name or none (a vendor-wide credential serves every
 * API of its vendor). Their names are the ones a caller picks between with
 * `Jentic-Credential-Name`, so a new name should differ from all of them.
 */
export function credentialsSharingApi(
	credentials: readonly Credential[],
	api: { vendor: string; name: string },
): Credential[] {
	const vendor = slugifyApiField(api.vendor);
	if (!vendor) return [];
	const name = slugifyApiField(api.name);
	return credentials.filter((cred) => {
		if (slugifyApiField(cred.api.vendor) !== vendor) return false;
		const credName = slugifyApiField(cred.api.name ?? '');
		return !name || !credName || credName === name;
	});
}

/** The credential in `siblings` already called `name`, if any. */
export function credentialNamed(
	siblings: readonly Credential[],
	name: string,
): Credential | undefined {
	const key = nameKey(name);
	if (!key) return undefined;
	return siblings.find((cred) => nameKey(cred.name) === key);
}

/**
 * `name` when no sibling holds it, else the first free `name 2`, `name 3`, …
 * A name that already ends in a number counts on from it, so a clash on
 * `airlabs.co 2` suggests `airlabs.co 3` rather than `airlabs.co 2 2`.
 */
export function suggestUniqueName(name: string, siblings: readonly Credential[]): string {
	const trimmed = name.trim();
	const taken = new Set(siblings.map((cred) => nameKey(cred.name)));
	if (!taken.has(nameKey(trimmed))) return trimmed;
	const match = /^(.*\S)\s+(\d+)$/.exec(trimmed);
	const base = match ? match[1] : trimmed;
	let n = match ? Number(match[2]) + 1 : 2;
	while (taken.has(nameKey(`${base} ${n}`))) n += 1;
	return `${base} ${n}`;
}

/** A name another credential for the API already holds, with a free one to use instead. */
export interface CredentialNameClash {
	clash: Credential;
	suggestion: string;
}

/**
 * Whether `name` repeats a credential that shares `api` — `excludeId` leaves out
 * the credential being renamed, so its own current name never clashes with itself.
 */
export function credentialNameClash(
	credentials: readonly Credential[],
	api: { vendor: string; name: string },
	name: string,
	excludeId?: string,
): CredentialNameClash | null {
	const siblings = credentialsSharingApi(credentials, api).filter(
		(cred) => cred.credential_id !== excludeId,
	);
	const clash = credentialNamed(siblings, name);
	return clash ? { clash, suggestion: suggestUniqueName(name, siblings) } : null;
}
