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

/** Group key for "the same API": vendor and name in slug form, version ignored,
 * so a credential re-added for a new revision lands beside its predecessor. */
export function credentialApiGroupKey(cred: Pick<Credential, 'api'>): string {
	return `${slugifyApiField(cred.api.vendor)}/${slugifyApiField(cred.api.name ?? '')}`;
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
