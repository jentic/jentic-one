import { Cloud, KeyRound, LockOpen, LogIn, Ticket, UserRound, type LucideIcon } from 'lucide-react';
import { Tag } from '@/shared/ui';
import {
	CredentialType,
	CREDENTIAL_TYPE_LABELS,
	credentialDetails,
	type Credential,
} from '@/shared/credentials/api';

const ICON: Record<CredentialType, LucideIcon> = {
	[CredentialType.BEARER_TOKEN]: Ticket,
	[CredentialType.API_KEY]: KeyRound,
	[CredentialType.BASIC]: UserRound,
	[CredentialType.OAUTH2]: LogIn,
	[CredentialType.NO_AUTH]: LockOpen,
	[CredentialType.SIGV4]: Cloud,
};

const OAUTH_GRANT_LABEL: Record<string, string> = {
	authorization_code: 'Authorization Code',
	client_credentials: 'Client Credentials',
	device_code: 'Device Code',
};

type CredentialTypeBadgeProps = { type: CredentialType } | { credential: Credential };

/**
 * Neutral tag that names a credential's auth type; the icon tells the types
 * apart. Given the whole credential, an OAuth 2.0 tag also names its grant
 * (Device Code / Authorization Code / Client Credentials) so two OAuth
 * credentials for one API are told apart at a glance.
 */
export function CredentialTypeBadge(props: CredentialTypeBadgeProps) {
	const type = 'credential' in props ? props.credential.type : props.type;
	let label = CREDENTIAL_TYPE_LABELS[type] ?? type;
	if ('credential' in props && type === CredentialType.OAUTH2) {
		const grant = credentialDetails(props.credential).grant_type;
		const suffix = grant ? OAUTH_GRANT_LABEL[grant] : undefined;
		if (suffix) label = `${label} · ${suffix}`;
	}
	return <Tag icon={ICON[type]}>{label}</Tag>;
}
