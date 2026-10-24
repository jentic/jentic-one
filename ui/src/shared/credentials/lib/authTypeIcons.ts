// The one glyph per authentication type. Every surface that names an auth type
// — a credential's type tag, the method picker, a spec's declared schemes —
// takes its icon from here, so a type reads the same wherever it shows.
// Decorative only: the type's label stays the accessible name.
import { Cloud, KeyRound, LockOpen, LogIn, Ticket, UserRound, type LucideIcon } from 'lucide-react';
import { CredentialType } from '@/shared/credentials/api';

/** Icon for each credential type. */
export const CREDENTIAL_TYPE_ICONS: Record<CredentialType, LucideIcon> = {
	[CredentialType.BEARER_TOKEN]: Ticket,
	[CredentialType.API_KEY]: KeyRound,
	[CredentialType.BASIC]: UserRound,
	[CredentialType.OAUTH2]: LogIn,
	[CredentialType.NO_AUTH]: LockOpen,
	[CredentialType.SIGV4]: Cloud,
};

/** A credential of a type this map doesn't know yet: the generic key. */
export const FALLBACK_AUTH_TYPE_ICON: LucideIcon = KeyRound;

/** Icon for a credential type; the generic key for a value the UI doesn't know. */
export function credentialTypeIcon(type: CredentialType | string): LucideIcon {
	return Object.prototype.hasOwnProperty.call(CREDENTIAL_TYPE_ICONS, type)
		? CREDENTIAL_TYPE_ICONS[type as CredentialType]
		: FALLBACK_AUTH_TYPE_ICON;
}

/**
 * A declared scheme's type, as the API layers spell it (`bearer`, `apiKey` or
 * its snake_case form, `basic`, `oauth2`; any case), to the credential type it
 * asks for.
 */
const SCHEME_TOKEN_TYPE: Record<string, CredentialType> = {
	bearer: CredentialType.BEARER_TOKEN,
	apikey: CredentialType.API_KEY,
	api_key: CredentialType.API_KEY,
	basic: CredentialType.BASIC,
	oauth2: CredentialType.OAUTH2,
};

/** Icon for a declared scheme type; the generic key for one with no credential type. */
export function schemeTypeIcon(scheme: string): LucideIcon {
	const key = scheme.trim().toLowerCase();
	return Object.prototype.hasOwnProperty.call(SCHEME_TOKEN_TYPE, key)
		? CREDENTIAL_TYPE_ICONS[SCHEME_TOKEN_TYPE[key]]
		: FALLBACK_AUTH_TYPE_ICON;
}
