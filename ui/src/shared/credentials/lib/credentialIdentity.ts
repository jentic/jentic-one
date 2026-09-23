// What tells one credential apart from another. An API can hold several, and
// their names often repeat the API's own ("airlabs.co" three times), so every
// surface that lists siblings leans on the same facts: auth type, when it was
// added, and a short id tail.
import { slugifyApiField } from '@/shared/lib/apiSlug';
import { CREDENTIAL_TYPE_LABELS, type Credential } from '@/shared/credentials/api';

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
